"""一个 Graph 节点内完成有限决策循环；工具、校验与展示保持独立函数。"""
import asyncio
import json

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langgraph.runtime import Runtime

from app.agent.anomaly_agent.handlers.dimension_contribution import query_dimension
from app.agent.anomaly_agent.schemas import (
    AnalysisAction, AnalysisContext, AnalysisObservation, ChangeAnalysisReport,
)
from app.agent.anomaly_agent.skills.base import validate_strategy
from app.agent.anomaly_agent.scope_planning import (
    available_actions, validate_action, scope_depth, VISIBLE_GROUPS,
)
from app.agent.anomaly_agent.state import AnomalyState
from app.agent.data_agent.context import DataAgentContext
from app.core.llm import get_context_llm
from app.core.logging import logger
from app.prompt.prompt_loader import load_prompt


MAX_ROUNDS = 3
TOTAL_TIMEOUT_SECONDS = 240
DECISION_TIMEOUT_SECONDS = 45
TOOL_TIMEOUT_SECONDS = 90


async def decide_action(context: AnalysisContext, candidates: list[str],
                        observations: list[AnalysisObservation], rounds_left: int,
                        runtime: Runtime[DataAgentContext]) -> AnalysisAction:
    parser = JsonOutputParser(pydantic_object=AnalysisAction)
    prompt = ChatPromptTemplate.from_template(load_prompt("anomaly/decide_action"))
    history = []
    for observation in observations:
        item = observation.model_dump(mode="json", exclude={"sql_by_period"})
        item["group_count"] = len(item["groups"])
        item["groups"] = item["groups"][:VISIBLE_GROUPS]
        history.append(item)
    menu = available_actions(context, candidates, observations)
    payload = dict(context=context.model_dump(mode="json"),
                   candidate_dimensions=candidates,
                   available_actions=[a.model_dump(mode="json", exclude={"reason"}) for a in menu],
                   observations=history,
                   rounds_left=rounds_left, max_period_queries_per_action=2)
    chain = prompt | get_context_llm(runtime, temperature=0.0, streaming=False) | parser
    raw = await chain.ainvoke({"format_instructions": parser.get_format_instructions(),
                              "analysis_context": json.dumps(payload, ensure_ascii=False)})
    return AnalysisAction.model_validate(raw)


def render_summary(context: AnalysisContext, observations: list[AnalysisObservation], stop_reason: str) -> str:
    lines = [context.verification.summary]
    names = {d.column: d.name for d in context.metric.dimensions}
    for observation in observations:
        if observation.status != "ok":
            lines.append(f"{observation.observation_id}：{observation.summary}")
            continue
        dimension = next(d.name for d in context.metric.dimensions
                         if d.column == observation.action.dimension_column)
        scope_text = "、".join(f"{names.get(f.dimension, f.dimension)}{f.operator}{f.value}"
                              for f in observation.scope_filters) or "原请求范围"
        lines.append(f"[{observation.observation_id}] 范围：{scope_text}；按{dimension}分析，"
                     "两期分组汇总与当前范围基准对账通过。")
        if observation.baseline_change_amount is not None:
            lines.append(f"当前范围净变化：{observation.baseline_change_amount}；贡献率分母为该范围净变化，"
                         "不能与父级或其他维度贡献相加。")
        for group in sorted(observation.groups, key=lambda g: g.change_amount)[:3]:
            lines.append(f"{group.group_value if group.group_value is not None else '（NULL）'}："
                         f"{group.comparison_value} → {group.current_value}，差额 {group.change_amount}。")
    labels = {
        "model_finished": "模型选择结束本轮分析", "budget_exhausted": "达到轮数上限",
        "no_candidates": "无可用的未尝试维度", "invalid_action": "模型动作不合法，已停止",
        "decision_error": "模型决策失败，已停止", "timeout": "达到执行时限",
        "verification_stopped": "变化核实未通过",
    }
    lines.append(f"停止原因：{labels[stop_reason]}。")
    lines.append("结果仅定位变化来源，不证明业务因果；对账不能替代 SQL 口径、关联粒度和数据完整性审核。")
    return "\n".join(lines)


async def analyze_change(state: AnomalyState, runtime: Runtime[DataAgentContext]) -> dict[str, object]:
    """State 只写入最终报告；中间过程通过 custom 事件反馈，不具备轮次级恢复。"""
    context = AnalysisContext(request=state["analysis_request"], metric=state["metric_context"],
                              comparison=state["analysis_result"], verification=state["change_verification"])
    strategy = state.get("analysis_strategy")
    decisions, observations = [], []
    reason = "no_candidates"
    if context.verification.decision != "continue":
        reason = "verification_stopped"
    elif strategy is not None and strategy.status == "ready":
        validate_strategy(context, strategy)
        # 第一版不开放标识符维度，避免模型直接选择高基数用户 ID。
        candidates = [s.dimension_column for s in strategy.steps
                      if not s.dimension_column.rsplit(".", 1)[-1].lower().endswith("_id")]
        try:
            async with asyncio.timeout(TOTAL_TIMEOUT_SECONDS):
                for round_index in range(MAX_ROUNDS):
                    if not available_actions(context, candidates, observations):
                        reason = "no_candidates"
                        break
                    try:
                        action = await asyncio.wait_for(
                            decide_action(context, candidates, observations, MAX_ROUNDS - round_index, runtime),
                            timeout=DECISION_TIMEOUT_SECONDS,
                        )
                    except TimeoutError:
                        reason = "timeout"
                        break
                    except Exception:
                        logger.exception("异动分析决策失败")
                        reason = "decision_error"
                        break
                    decisions.append(action)
                    runtime.stream_writer({"type": "analysis_action", "agent": "anomaly",
                                           "round": round_index + 1, **action.model_dump(mode="json")})
                    if action.action == "finish":
                        reason = "model_finished"
                        break
                    try:
                        scoped_context = validate_action(context, action, candidates, observations)
                    except ValueError:
                        reason = "invalid_action"
                        break
                    # 按“有效筛选范围＋维度”判重，同一维度允许在不同分组内查询。
                    observation_id = f"dimension_{len(observations) + 1}"
                    try:
                        groups, sqls = await asyncio.wait_for(
                            query_dimension(scoped_context, action.dimension_column, runtime),
                            timeout=TOOL_TIMEOUT_SECONDS,
                        )
                        observation = AnalysisObservation(
                            observation_id=observation_id, action=action, status="ok",
                            summary="两期分组与当前范围基准对账通过；仍需审查 SQL 口径与 Join 粒度。",
                            groups=groups, sql_by_period=sqls,
                        )
                    except Exception as exc:
                        logger.exception("异动维度工具失败：{}", action.dimension_column)
                        observation = AnalysisObservation(
                            observation_id=observation_id, action=action, status="error",
                            summary=("查询超时，不能形成该维度结论。" if isinstance(exc, TimeoutError)
                                     else "查询或结果校验失败，不能形成该维度结论；请检查日志。"),
                        )
                    observation = observation.model_copy(update={
                        "scope_filters": list(scoped_context.request.filters),
                        "scope_depth": scope_depth(action, observations),
                        "baseline_current_value": scoped_context.comparison.current_value,
                        "baseline_comparison_value": scoped_context.comparison.comparison_value,
                        "baseline_change_amount": scoped_context.comparison.change_amount,
                    })
                    observations.append(observation)
                    runtime.stream_writer({"type": "analysis_observation", "agent": "anomaly",
                                           **observation.model_dump(mode="json")})
                else:
                    reason = "budget_exhausted"
        except TimeoutError:
            reason = "timeout"
    report = ChangeAnalysisReport(
        request_id=context.request.request_id, stop_reason=reason, decisions=decisions,
        observations=observations, summary=render_summary(context, observations, reason),
    )
    return {"change_analysis_report": report}
