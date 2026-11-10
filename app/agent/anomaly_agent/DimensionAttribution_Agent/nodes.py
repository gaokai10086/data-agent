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
)
from app.agent.anomaly_agent.DimensionAttribution_Agent.state import DimensionAttributionState
from app.agent.anomaly_agent.DimensionAttribution_Agent.tools import (
    ATTRIBUTION_TOOL_REGISTRY,
    DimensionPeriodComparisonTool,
)
from app.core.llm import get_context_llm


_STOP_REASONS = {
    "completed", "budget_exhausted", "insufficient_evidence", "max_depth_reached",
    "no_candidate_dimensions", "decision_failed", "illegal_decision", "duplicate_task",
    "ambiguous_dimension", "duplicate_dimension", "execution_failed", "no_tools_available",
}


def _explicit_dimensions(input_data):
    return DimensionPeriodComparisonTool.find_dimension_matches(input_data)


def _candidate_dimensions(state: DimensionAttributionState):
    input_data = state["input"]
    explicit = _explicit_dimensions(input_data)
    if len(explicit) > 1:
        return [], "ambiguous_dimension"
    dimensions = explicit if explicit else list(input_data.metric_context.dimensions)
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
    parent_columns = {item.dimension_column for item in state.get("parent_scope", [])}
    candidates = [
        item for item in dimensions
        if item.column not in fixed_columns
        and (explicit or item.column not in determined_columns)
        and item.column not in analyzed
        and item.column not in parent_columns
    ]
    return candidates, None


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
    parent_scope = list(state.get("parent_scope", []))
    task_id = DimensionPeriodComparisonTool.build_task_id(
        state["input"], dimension.column, parent_scope, state.get("current_depth", 0)
    )
    return AttributionDecision(
        action="execute_tool", tool_name="dimension_period_comparison",
        dimension_column=dimension.column, parent_scope=parent_scope,
        depth=state.get("current_depth", 0), task_id=task_id,
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

    if rounds > strategy.max_analysis_rounds:
        return finish("达到最大分析轮次", "budget_exhausted")
    if input_data.inspection_outcome.status != strategy.ready_status:
        return finish("ChangeInspection 尚未 ready", "verification_not_ready")
    if state.get("query_count", 0) >= strategy.max_query_budget:
        return finish("达到查询预算", "budget_exhausted")
    if len(state.get("analyzed_dimensions", [])) >= strategy.max_dimensions:
        return finish("达到最大分析维度数量", "budget_exhausted")
    if state.get("current_depth", 0) > strategy.max_depth:
        return finish("达到最大下钻深度", "max_depth_reached")
    if any(item.status == "failed" for item in observations):
        return finish("上一轮工具执行失败", "execution_failed")

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

    allowed = {item.column: item for item in candidates}
    if decision.tool_name not in executable:
        return finish("LLM 选择了未注册工具", "illegal_decision")
    if decision.dimension_column not in allowed:
        return finish("LLM 选择了未授权维度", "illegal_decision")
    current_depth = state.get("current_depth", 0)
    if decision.depth not in {current_depth, current_depth + 1}:
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
        payload = await tool.run(
            state["input"], runtime, dimension_column=decision.dimension_column,
            parent_scope=decision.parent_scope, task_id=decision.task_id, depth=decision.depth,
            overall=_scoped_overall(state, decision.parent_scope),
        )
        observation = AttributionObservation(
            observation_id=uuid.uuid4().hex, tool_name=tool.name, status="ok",
            dimension_column=payload["dimension"].column, task_id=payload["task_id"],
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
        path = AttributionPath(
            task_id=payload["task_id"], depth=payload["depth"],
            dimension_column=payload["dimension"].column, parent_scope=payload["parent_scope"],
            attributions=payload["members"], reconciliation=payload["reconciliation"],
            sql_by_period=payload["sql_by_period"],
            informative=payload["informative"], null_member_present=payload["null_member_present"],
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
            "current_depth": payload["depth"],
            "parent_scope": payload["parent_scope"],
        }
    except Exception as exc:
        observation = AttributionObservation(
            observation_id=uuid.uuid4().hex, tool_name=tool.name, status="failed",
            dimension_column=decision.dimension_column, task_id=decision.task_id,
            depth=decision.depth, parent_scope=decision.parent_scope, error=str(exc),
        )
        return {"observations": state.get("observations", []) + [observation], "stop_reason": "execution_failed"}


def finalize_attribution(state: DimensionAttributionState) -> dict:
    stop_reason = state.get("stop_reason") or "completed"
    failed = stop_reason in {"execution_failed", "decision_failed"}
    has_paths = bool(state.get("paths", []))
    status = "failed" if failed else ("completed" if has_paths or stop_reason == "completed" else "unavailable")
    completion_status = (
        "none" if not has_paths else
        "complete" if stop_reason in {"completed", "no_candidate_dimensions"} else
        "partial"
    )
    reason = "execution_failed" if failed and stop_reason == "execution_failed" else (
        "completed" if status == "completed" else stop_reason
    )
    result = AttributionResult(
        request_id=state["request_id"], status=status, completion_status=completion_status, reason=reason,
        stop_reason=stop_reason, analyzed_dimensions=state.get("analyzed_dimensions", []),
        decisions=state.get("decisions", []), observations=state.get("observations", []),
        summary="维度归因完成，已返回真实查询证据。" if has_paths else "未生成可用的维度归因证据。",
        total_query_count=state.get("query_count", 0), attributions=state.get("attributions", []),
        reconciliation=state.get("reconciliation"), paths=state.get("paths", []),
    )
    return {"result": result, "decisions": result.decisions}
