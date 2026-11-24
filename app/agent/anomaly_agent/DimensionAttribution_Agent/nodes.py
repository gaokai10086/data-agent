"""DimensionAttributionAgent 的动态决策、工具执行和结果收敛节点。"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from types import SimpleNamespace

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate

from app.agent.anomaly_agent.DimensionAttribution_Agent.schemas import (
    AttributionDecision,
    AttributionObservation,
    AttributionPath,
    AttributionResult,
    AttributionTask,
    DimensionScope,
)
from app.agent.anomaly_agent.DimensionAttribution_Agent.state import DimensionAttributionState
from app.agent.anomaly_agent.DimensionAttribution_Agent.tools import (
    ATTRIBUTION_TOOL_REGISTRY,
    AttributionQueryError,
    DimensionPeriodComparisonTool,
)
from app.core.llm import get_context_llm
from app.agent.anomaly_agent.DimensionAttribution_Agent.scheduler import DynamicTaskScheduler, RequestBudget, TaskExecutionResult, ParentEvidence


_STOP_REASONS = {
    "completed", "budget_exhausted", "insufficient_evidence", "max_depth_reached",
    "no_candidate_dimensions", "decision_failed", "illegal_decision", "duplicate_task",
    "ambiguous_dimension", "duplicate_dimension", "execution_failed", "no_tools_available",
}


def select_decline_members(members, threshold: Decimal, max_children: int):
    """Select a stable prefix of actual negative members; positive changes only offset net change."""
    if not Decimal("0") < threshold <= Decimal("1") or max_children < 1:
        raise ValueError("下降覆盖率阈值或分支上限无效")
    declines = sorted(
        (member for member in members if member.change_amount is not None and member.change_amount < 0),
        key=lambda member: (-abs(member.change_amount), str(member.group_value or "")),
    )
    total = sum((abs(member.change_amount) for member in declines), Decimal("0"))
    eligible = [member for member in declines if member.group_value is not None]
    selected = []
    covered = Decimal("0")
    for member in eligible:
        if len(selected) >= max_children or covered / total >= threshold:
            break
        selected.append(member)
        covered += abs(member.change_amount)
    actual = covered / total if total else Decimal("0")
    reason = ("no_decline_members" if not declines else
              "null_member_unselectable" if not eligible else
              "child_limit_reached" if actual < threshold and len(selected) >= max_children else
              "unreachable_coverage" if actual < threshold else "coverage_reached")
    return selected, total, actual, total - covered, reason


def _explicit_dimensions(input_data):
    return DimensionPeriodComparisonTool.find_dimension_matches(input_data)


def _candidate_dimensions(state: DimensionAttributionState):
    input_data = state["input"]
    explicit = _explicit_dimensions(input_data)
    if len(explicit) > 1:
        return [], "ambiguous_dimension"
    dimensions = explicit if explicit else list(input_data.metric_context.dimensions)
    fixed_path = tuple(getattr(input_data.skill_strategy, "dimension_path", ()) or ())
    # 有真实 current_scope 时执行固定路径；保留旧的无 scope 契约测试/上游调用兼容。
    if fixed_path and not explicit and (state.get("current_scope") or not state.get("analyzed_dimensions")):
        analyzed = set(state.get("analyzed_dimensions", []))
        current_scope = state.get("current_scope", state.get("parent_scope", []))
        next_index = len(current_scope)
        if not current_scope:
            analyzed_path = [index for index, column in enumerate(fixed_path) if column in analyzed]
            if analyzed_path:
                next_index = max(analyzed_path) + 1
        if next_index >= len(fixed_path):
            return [], None
        dimensions = [item for item in input_data.metric_context.dimensions if item.column == fixed_path[next_index]]
    fixed_names = {item.dimension for item in input_data.request.filters if item.dimension}
    fixed_columns = {
        item.column for item in input_data.metric_context.dimensions
        if item.name in fixed_names or any(alias in fixed_names for alias in item.aliases)
    }
    determined_columns = {
        determined
        for item in input_data.metric_context.dimensions
        if item.column in fixed_columns
        for determined in item.determines
    }
    analyzed = set(state.get("analyzed_dimensions", []))
    parent_columns = {item.dimension_column for item in state.get("current_scope", state.get("parent_scope", []))}
    candidates = [
        item for item in dimensions
        if item.column not in fixed_columns
        and (explicit or item.column not in determined_columns)
        and item.column not in analyzed
        and item.column not in parent_columns
    ]
    return candidates, None


def _next_branch_dimension(input_data, scope, current_dimension):
    """Choose the next Meta dimension for a verified branch, independently of siblings."""
    fixed_path = tuple(input_data.skill_strategy.dimension_path or ())
    used = {item.dimension_column for item in scope} | {current_dimension}
    fixed_names = {item.dimension for item in input_data.request.filters if item.dimension}
    fixed_columns = {item.column for item in input_data.metric_context.dimensions
                     if item.name in fixed_names or any(alias in fixed_names for alias in item.aliases)}
    if fixed_path:
        try:
            next_column = fixed_path[fixed_path.index(current_dimension) + 1]
        except (ValueError, IndexError):
            return None
        return next((item for item in input_data.metric_context.dimensions
                     if item.column == next_column and item.column not in fixed_columns and item.column not in used), None)
    return next((item for item in input_data.metric_context.dimensions
                 if item.column not in used and item.column not in fixed_columns), None)


def _observed_member_values(state: DimensionAttributionState, dimension_column: str):
    values = set()
    for observation in state.get("observations", []):
        if observation.status != "ok" or observation.dimension_column != dimension_column:
            continue
        for item in observation.evidence.get("members", []):
            values.add(item.get("group_value"))
    return values

def _scoped_overall(state: DimensionAttributionState, parent_scope):
    """用父级真实 Observation 的成员值作为下钻层对账基准。"""
    if not parent_scope:
        return state["input"].inspection_outcome.overall_result
    target = parent_scope[-1]
    for observation in reversed(state.get("observations", [])):
        if observation.status != "ok" or observation.dimension_column != target.dimension_column:
            continue
        if observation.parent_scope != parent_scope[:-1]:
            continue
        for member in observation.evidence.get("members", []):
            if member.get("group_value") == target.member_value:
                return SimpleNamespace(
                    current_value=Decimal(str(member.get("current_value"))),
                    comparison_value=Decimal(str(member.get("comparison_value"))),
                    change_amount=Decimal(str(member.get("change_amount"))),
                )
    raise ValueError("缺少父级成员的两个周期真实值，不能进行下钻对账")


def _decision_prompt(state: DimensionAttributionState, candidates, parser):
    input_data = state["input"]
    dimensions = [
        {"name": item.name, "column": item.column, "aliases": item.aliases}
        for item in candidates
    ]
    observations = [item.model_dump(mode="json") for item in state.get("observations", [])]
    return {
        "format_instructions": parser.get_format_instructions(),
        "raw_question": input_data.raw_question,
        "dimensions": json.dumps(dimensions, ensure_ascii=False),
        "tools": json.dumps(list(input_data.available_tool_names), ensure_ascii=False),
        "observations": json.dumps(observations, ensure_ascii=False, default=str),
        "analyzed_tasks": json.dumps(state.get("analyzed_task_ids", []), ensure_ascii=False),
        "round": state.get("round_count", 0) + 1,
        "max_rounds": input_data.skill_strategy.max_analysis_rounds,
        "max_dimensions": input_data.skill_strategy.max_dimensions,
        "query_count": state.get("query_count", 0),
        "max_queries": input_data.skill_strategy.max_query_budget,
        "depth": state.get("current_depth", 0),
        "max_depth": input_data.skill_strategy.max_depth,
    }


def _fallback_decision(state, candidates):
    """没有 Runtime/LLM 时仅用于契约测试的确定性选择。"""
    if not candidates:
        return AttributionDecision(action="finish", reason="没有新的合法维度", stop_reason="no_candidate_dimensions")
    dimension = candidates[0]
    parent_scope = list(state.get("current_scope", state.get("parent_scope", [])))
    depth = len(parent_scope)
    task_id = DimensionPeriodComparisonTool.build_task_id(
        state["input"], dimension.column, parent_scope, depth
    )
    return AttributionDecision(
        action="execute_tool", tool_name="dimension_period_comparison",
        dimension_column=dimension.column, parent_scope=parent_scope,
        depth=depth, task_id=task_id,
        reason="确定性测试回退选择 Meta 允许维度",
    )


async def main_reason(state: DimensionAttributionState, runtime=None) -> dict:
    """调用结构化 LLM 决策，并用程序规则验证每个字段。"""
    input_data = state["input"]
    strategy = input_data.skill_strategy
    rounds = state.get("round_count", 0) + 1
    observations = state.get("observations", [])
    decisions = state.get("decisions", [])
    base = {
        "request_id": input_data.request.request_id,
        "observations": observations,
        "decisions": decisions,
        "round_count": rounds,
    }

    def finish(reason: str, stop_reason: str):
        decision = AttributionDecision(action="finish", reason=reason, stop_reason=stop_reason)
        return {**base, "decisions": decisions + [decision], "decision": decision, "stop_reason": stop_reason}

    if rounds >= strategy.max_analysis_rounds:
        return finish("达到最大分析轮次", "budget_exhausted")
    if input_data.inspection_outcome.status != strategy.ready_status:
        return finish("ChangeInspection 尚未 ready", "verification_not_ready")
    if state.get("query_count", 0) + 2 > strategy.max_query_budget:
        return finish("达到查询预算", "budget_exhausted")
    if len(state.get("analyzed_task_ids", [])) >= strategy.max_attribution_tasks:
        return finish("达到最大维度归因任务数", "budget_exhausted")
    if not state.get("paths") and len(state.get("analyzed_dimensions", [])) >= strategy.max_dimensions:
        return finish("达到最大分析维度数量", "budget_exhausted")
    if not state.get("paths") and state.get("current_depth", 0) > strategy.max_depth:
        return finish("达到最大下钻深度", "max_depth_reached")
    if not state.get("paths") and any(item.status == "failed" for item in observations):
        return finish("上一轮工具执行失败", "execution_failed")

    if state.get("paths"):
        pending = list(state.get("pending_tasks", []))
        if not pending:
            return finish("所有已验证分支已处理", "completed")
        task = pending[0]
        if task.depth > strategy.max_depth:
            return finish("达到最大下钻深度", "max_depth_reached")
        if task.task_id in state.get("analyzed_task_ids", []):
            return finish("相同维度、范围和周期已经分析过", "duplicate_task")
        decision = AttributionDecision(
            action="execute_tool", tool_name="dimension_period_comparison",
            dimension_column=task.dimension_column, parent_scope=task.parent_scope,
            depth=task.depth, task_id=task.task_id, parent_task_id=task.parent_task_id,
            reason="按已验证下降成员队列逐分支分析",
        )
        return {**base, "decisions": decisions + [decision], "decision": decision,
                "pending_tasks": pending[1:]}

    candidates, candidate_error = _candidate_dimensions(state)
    if candidate_error:
        return finish("用户指定的维度存在歧义", candidate_error)
    executable = [name for name in input_data.available_tool_names if name in ATTRIBUTION_TOOL_REGISTRY]
    if not executable:
        return finish("当前没有已注册的维度归因工具", "no_tools_available")
    if not candidates:
        return finish("没有新的合法维度", "no_candidate_dimensions")
    try:
        if runtime is None or not getattr(runtime, "context", None):
            decision = _fallback_decision(state, candidates)
        else:
            prompt = ChatPromptTemplate.from_template(
                """你是维度归因 Agent 的决策器。只能从 dimensions 中选择维度，不能编造物理字段或成员。\n
用户问题：{raw_question}\n允许工具（tool_name 必须逐字使用其中一个）：{tools}\n允许维度：{dimensions}\n真实历史 Observation：{observations}\n已完成任务：{analyzed_tasks}\n
当前轮次 {round}/{max_rounds}，已用查询 {query_count}/{max_queries}，当前深度 {depth}/{max_depth}。\n
规则：明确指定的维度优先；下钻时 parent_scope 中的成员必须来自真实 Observation；不能重复任务；若无合法动作则 finish。\n{format_instructions}"""
            )
            parser = JsonOutputParser(pydantic_object=AttributionDecision)
            llm = get_context_llm(runtime, temperature=0.0, streaming=False, max_retries=0)
            decision = await (prompt | llm | parser).ainvoke(_decision_prompt(state, candidates, parser))
            decision = AttributionDecision.model_validate(decision)
    except Exception as exc:
        return finish(f"LLM 决策失败：{type(exc).__name__}", "decision_failed")

    if decision.action == "finish":
        stop_reason = decision.stop_reason or "completed"
        if stop_reason not in _STOP_REASONS:
            stop_reason = "completed"
        return {**base, "decisions": decisions + [decision], "decision": decision, "stop_reason": stop_reason}

    # 首轮保留旧调用契约；子任务的父级范围全部由真实成员队列构造。
    expected_scope = list(state.get("current_scope", state.get("parent_scope", [])))
    if not expected_scope:
        expected_scope = list(decision.parent_scope)
    decision = decision.model_copy(update={"parent_scope": expected_scope, "depth": len(expected_scope)})

    allowed = {item.column: item for item in candidates}
    if decision.tool_name not in executable:
        return finish("LLM 选择了未注册工具", "illegal_decision")
    if decision.dimension_column not in allowed:
        return finish("LLM 选择了未授权维度", "illegal_decision")
    current_depth = state.get("current_depth", 0)
    if decision.depth != len(expected_scope):
        return finish("LLM 给出的下钻深度与 State 不一致", "illegal_decision")
    if decision.depth > strategy.max_depth:
        return finish("达到最大下钻深度", "max_depth_reached")
    if len(decision.parent_scope) != decision.depth:
        return finish("父级范围数量与下钻深度不一致", "illegal_decision")
    for scope in decision.parent_scope:
        if scope.member_value not in _observed_member_values(state, scope.dimension_column):
            return finish("下钻成员没有真实 Observation 证据", "insufficient_evidence")
    task_id = DimensionPeriodComparisonTool.build_task_id(
        input_data, decision.dimension_column, decision.parent_scope, decision.depth
    )
    if task_id in state.get("analyzed_task_ids", []):
        return finish("相同维度、父级范围和周期已经分析过", "duplicate_task")
    decision = decision.model_copy(update={"task_id": task_id})
    return {**base, "decisions": decisions + [decision], "decision": decision}


async def execute_attribution_tools(state: DimensionAttributionState, runtime=None) -> dict:
    """执行已选择工具；每次双周期查询扣减两个 SQL 配额。"""
    decision = state["decision"]
    if decision.action != "execute_tool":
        return {}
    tool = ATTRIBUTION_TOOL_REGISTRY.get(decision.tool_name or "")
    if tool is None:
        observation = AttributionObservation(
            observation_id=uuid.uuid4().hex, tool_name=decision.tool_name or "",
            status="unavailable", dimension_column=decision.dimension_column,
            task_id=decision.task_id, depth=decision.depth, parent_scope=decision.parent_scope,
            error="归因工具尚未实现",
        )
        return {"observations": state.get("observations", []) + [observation], "stop_reason": "no_tools_available"}
    try:
        remaining = state["input"].skill_strategy.max_query_budget - state.get("query_count", 0)
        if remaining < 2:
            raise AttributionQueryError("不足两次 SQL 查询预算", 0)
        payload = await tool.run(
            state["input"], runtime, dimension_column=decision.dimension_column,
            parent_scope=decision.parent_scope, task_id=decision.task_id, depth=decision.depth,
            overall=state.get("parent_overall") or _scoped_overall(state, decision.parent_scope),
            query_budget_remaining=remaining,
        )
        observation = AttributionObservation(
            observation_id=uuid.uuid4().hex, tool_name=tool.name, status="ok",
            dimension_column=payload["dimension"].column, task_id=payload["task_id"],
            parent_task_id=decision.parent_task_id,
            depth=payload["depth"], parent_scope=payload["parent_scope"],
            query_count=payload["query_count"],
            evidence={
                "members": [item.model_dump(mode="json") for item in payload["members"]],
                "reconciliation": payload["reconciliation"].model_dump(mode="json"),
                "sql_by_period": payload["sql_by_period"],
                "informative": payload["informative"],
                "null_member_present": payload["null_member_present"],
            },
        )
        selected, total_decline, coverage, uncovered, coverage_reason = select_decline_members(
            payload["members"], state["input"].skill_strategy.coverage_threshold,
            state["input"].skill_strategy.max_children_per_parent,
        )
        selected_names = [item.group_value for item in selected if item.group_value is not None]
        pending = list(state.get("pending_tasks", []))
        abandoned = list(state.get("abandoned_paths", []))
        strategy = state["input"].skill_strategy
        next_dimension = _next_branch_dimension(state["input"], decision.parent_scope, payload["dimension"].column)
        if payload["informative"] and decision.depth < strategy.max_depth and next_dimension:
            for member in selected:
                if member.group_value is None:
                    abandoned.append({"parent_task_id": payload["task_id"], "member": None, "reason": "null_member_scope"})
                    continue
                child_scope = list(decision.parent_scope) + [DimensionScope(
                    dimension_column=payload["dimension"].column, member_value=member.group_value,
                )]
                child_id = DimensionPeriodComparisonTool.build_task_id(
                    state["input"], next_dimension.column, child_scope, len(child_scope),
                )
                if child_id in state.get("analyzed_task_ids", []) or any(item.task_id == child_id for item in pending):
                    continue
                if len(state.get("analyzed_task_ids", [])) + 1 + len(pending) >= strategy.max_attribution_tasks:
                    abandoned.append({"parent_task_id": payload["task_id"], "member": member.group_value, "reason": "task_budget_exhausted"})
                    continue
                pending.append(AttributionTask(
                    task_id=child_id, parent_task_id=payload["task_id"],
                    dimension_column=next_dimension.column, parent_scope=child_scope, depth=len(child_scope),
                ))
        branch_stop = ("max_depth_reached" if decision.depth >= strategy.max_depth else
                       "no_candidate_dimensions" if next_dimension is None else
                       "uninformative" if not payload["informative"] else
                       coverage_reason)
        branch_status = "children_scheduled" if any(item.parent_task_id == payload["task_id"] for item in pending) else "stopped"
        path = AttributionPath(
            task_id=payload["task_id"], depth=payload["depth"],
            parent_task_id=decision.parent_task_id,
            dimension_column=payload["dimension"].column, parent_scope=payload["parent_scope"],
            attributions=payload["members"], reconciliation=payload["reconciliation"],
            sql_by_period=payload["sql_by_period"],
            informative=payload["informative"], null_member_present=payload["null_member_present"],
            selected_member=selected_names[0] if selected_names else None,
            selected_members=selected_names, target_decline_coverage=strategy.coverage_threshold,
            actual_decline_coverage=coverage, total_decline_amount=total_decline,
            uncovered_decline_amount=uncovered, branch_status=branch_status,
            branch_stop_reason=branch_stop,
        )
        sql_by_period = dict(state.get("sql_by_period", {}))
        for period, sql in payload["sql_by_period"].items():
            sql_by_period[f"{payload['task_id']}:{period}"] = sql
        analyzed_dimensions = list(state.get("analyzed_dimensions", []))
        if payload["dimension"].column not in analyzed_dimensions:
            analyzed_dimensions.append(payload["dimension"].column)
        return {
            "observations": state.get("observations", []) + [observation],
            "query_count": state.get("query_count", 0) + payload["query_count"],
            "analyzed_dimensions": analyzed_dimensions,
            "analyzed_task_ids": state.get("analyzed_task_ids", []) + [payload["task_id"]],
            "attributions": payload["members"], "reconciliation": payload["reconciliation"],
            "sql_by_period": sql_by_period,
            "paths": state.get("paths", []) + [path],
            "pending_tasks": pending,
            "abandoned_paths": abandoned,
            "current_depth": payload["depth"],
            "parent_scope": payload["parent_scope"],
            "current_scope": payload["parent_scope"] + [
                DimensionScope(dimension_column=payload["dimension"].column, member_value=(
                    selected_names[0] if selected_names else None
                ))
            ],
        }


    except Exception as exc:
        observation = AttributionObservation(
            observation_id=uuid.uuid4().hex, tool_name=tool.name, status="failed",
            dimension_column=decision.dimension_column, task_id=decision.task_id,
            parent_task_id=decision.parent_task_id,
            depth=decision.depth, parent_scope=decision.parent_scope, error=str(exc),
        )
        pending = list(state.get("pending_tasks", []))
        return {
            "observations": state.get("observations", []) + [observation],
            "query_count": state.get("query_count", 0) + getattr(exc, "actual_sql_count", 0),
            "analyzed_task_ids": state.get("analyzed_task_ids", []) + [decision.task_id],
            "abandoned_paths": state.get("abandoned_paths", []) + [{
                "task_id": decision.task_id, "parent_task_id": decision.parent_task_id,
                "parent_scope": [item.model_dump(mode="json") for item in decision.parent_scope],
                "reason": "execution_failed", "error": str(exc),
            }],
            "stop_reason": "execution_failed" if not pending else None,
        }


async def execute_dynamic_scheduler(state: DimensionAttributionState, runtime=None) -> dict:
    """Execute queued evidence-derived branches through the production scheduler."""
    roots = list(state.get("pending_tasks", []))
    if not roots:
        return state
    budget = RequestBudget(state["input"].skill_strategy.max_query_budget)
    scheduler = DynamicTaskScheduler(max_workers=min(6, state["input"].skill_strategy.max_concurrent_branches), budget=budget)

    evidence_by_task: dict[str, ParentEvidence] = {}
    async def execute(task):
        decision = AttributionDecision(action="execute_tool", tool_name="dimension_period_comparison",
            dimension_column=task.dimension_column, parent_scope=task.parent_scope,
            depth=task.depth, task_id=task.task_id, parent_task_id=task.parent_task_id,
            reason="dynamic scheduler")
        local = dict(state); local["decision"] = decision; local["pending_tasks"] = []
        parent_evidence = evidence_by_task.get(task.task_id)
        if parent_evidence and not parent_evidence.reconciled:
            return TaskExecutionResult(task.task_id, task.parent_task_id, "blocked", error="parent_evidence_not_reconciled")
        if parent_evidence:
            local["parent_overall"] = SimpleNamespace(
                current_value=Decimal(str(parent_evidence.target_value)),
                comparison_value=Decimal(str(parent_evidence.comparison_value)),
                change_amount=Decimal(str(parent_evidence.change_amount)),
            )
        out = await execute_attribution_tools(local, runtime)
        ok = bool(out.get("observations") and out["observations"][-1].status == "ok")
        children = tuple(out.get("pending_tasks", [])) if ok else ()
        if ok:
            path = out.get("paths", [])[-1]
            rec = path.reconciliation
            if not (rec.comparison_matches and rec.current_matches and rec.change_matches):
                return TaskExecutionResult(task.task_id, task.parent_task_id, "failed", query_count=out.get("query_count", 0), error="parent_reconciliation_failed")
            for child in children:
                member = next((m for m in path.attributions if m.group_value == child.parent_scope[-1].member_value), None)
                if member is not None:
                    evidence_by_task[child.task_id] = ParentEvidence(
                        parent_task_id=task.task_id,
                        parent_scope=tuple(child.parent_scope),
                        target_value=member.current_value,
                        comparison_value=member.comparison_value,
                        change_amount=member.change_amount,
                        reconciled=True,
                    )
        return TaskExecutionResult(task.task_id, task.parent_task_id, "completed" if ok else "failed",
            query_count=out.get("query_count", 0), result=out, children=children)

    results = await scheduler.run(roots, task_id=lambda t: t.task_id, parent_id=lambda t: t.parent_task_id, execute=execute)
    merged = dict(state)
    merged.update({"pending_tasks": [], "observations": list(state.get("observations", [])),
                   "paths": list(state.get("paths", [])), "abandoned_paths": list(state.get("abandoned_paths", [])),
                   "analyzed_task_ids": list(state.get("analyzed_task_ids", [])), "query_count": state.get("query_count", 0)})
    for item in results:
        out = item.result or {}
        if isinstance(out, dict):
            merged["observations"].extend(out.get("observations", [])); merged["paths"].extend(out.get("paths", []))
            merged["abandoned_paths"].extend(out.get("abandoned_paths", [])); merged["analyzed_task_ids"].extend(out.get("analyzed_task_ids", []))
            merged["query_count"] += out.get("query_count", 0)
    merged["stop_reason"] = "completed" if not merged["abandoned_paths"] else "execution_failed"
    return merged


def finalize_attribution(state: DimensionAttributionState) -> dict:
    stop_reason = state.get("stop_reason") or (
        "budget_exhausted" if state.get("pending_tasks") else "completed"
    )
    failed = stop_reason in {"execution_failed", "decision_failed"}
    has_paths = bool(state.get("paths", []))
    status = "failed" if failed and not has_paths else ("completed" if has_paths or stop_reason == "completed" else "unavailable")
    completion_status = (
        "none" if not has_paths else
        "complete" if stop_reason in {"completed", "no_candidate_dimensions"} and not state.get("abandoned_paths") else
        "partial"
    )
    reason = "execution_failed" if failed and stop_reason == "execution_failed" else (
        "completed" if status == "completed" else stop_reason
    )
    result = AttributionResult(
        request_id=state["request_id"], status=status, completion_status=completion_status, reason=reason,
        stop_reason=stop_reason, analyzed_dimensions=state.get("analyzed_dimensions", []),
        decisions=state.get("decisions", []), observations=state.get("observations", []),
        summary=(f"维度归因已完成 {len(state.get('paths', []))} 个真实对账任务；"
                 "各分支金额仅在同一父级范围内比较。") if has_paths else "未生成可用的维度归因证据。",
        total_query_count=state.get("query_count", 0), attributions=state.get("attributions", []),
        reconciliation=state.get("reconciliation"), paths=state.get("paths", []),
        current_path=state.get("current_scope", []) if len(state.get("paths", [])) == 1 else [],
        abandoned_paths=state.get("abandoned_paths", []),
        pending_tasks=state.get("pending_tasks", []),
    )
    return {"result": result, "decisions": result.decisions}
