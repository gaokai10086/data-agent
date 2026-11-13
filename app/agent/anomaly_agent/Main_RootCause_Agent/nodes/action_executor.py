"""Action Executor：鉴权、参数构造、去重、超时与 Observation 生成。"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from langgraph.runtime import Runtime

from app.agent.anomaly_agent.Main_RootCause_Agent.actions import (
    ACTION_REGISTRY,
    MAX_ACTION_EXECUTIONS,
    get_action,
)
from app.agent.anomaly_agent.DimensionAttribution_Agent.schemas import DimensionAttributionInput
from app.agent.anomaly_agent.DimensionAttribution_Agent.tools import ATTRIBUTION_TOOL_REGISTRY
from app.agent.anomaly_agent.Main_RootCause_Agent.actions.registry import (
    AttributionExecution,
    BusinessDiagnosisExecution,
    DecompositionExecution,
)
from app.agent.anomaly_agent.BusinessDiagnosis_Agent.schemas import BusinessDiagnosisInput
from app.agent.anomaly_agent.MetricDecomposition_Agent.schemas import MetricDecompositionInput
from app.agent.anomaly_agent.Main_RootCause_Agent.parsing.task_request_conversion import (
    comparison_type_for_periods,
    normalize_filter,
)
from app.agent.anomaly_agent.Main_RootCause_Agent.skills.registry import (
    load_skill,
    resolve_skill_capabilities,
)
from app.agent.anomaly_agent.Main_RootCause_Agent.state import RootCauseState
from app.agent.anomaly_agent.schemas import (
    AnalysisRequest,
    FilterCondition,
    InspectionOutcome,
    MonthPeriod,
)
from app.agent.data_agent.context import DataAgentContext


ACTION_TIMEOUT_SECONDS = 60


def _attribution_timeout() -> float:
    """允许部署配置归因 Action 总超时，同时保持上下界。"""
    try:
        value = float(os.getenv("DIMENSION_ATTRIBUTION_TIMEOUT_SECONDS", "600"))
    except ValueError:
        value = 600.0
    return min(max(value, 60.0), 1200.0)


def _decomposition_timeout() -> float:
    """指标拆解使用独立的有界超时，避免占用主 Agent 的无限等待。"""
    try:
        value = float(os.getenv("METRIC_DECOMPOSITION_TIMEOUT_SECONDS", "300"))
    except ValueError:
        value = 300.0
    return min(max(value, 60.0), 1200.0)


def _business_diagnosis_timeout() -> float:
    try:
        value = float(os.getenv("BUSINESS_DIAGNOSIS_TIMEOUT_SECONDS", "300"))
    except ValueError:
        value = 300.0
    return min(max(value, 60.0), 1200.0)


def _build_analysis_request(parsed_request: dict[str, Any]) -> AnalysisRequest:
    metric = str(parsed_request.get("metric") or "").strip()
    if not metric:
        raise ValueError("parsed_request 缺少指标")
    target = MonthPeriod.model_validate(parsed_request["target_period"])
    comparison = MonthPeriod.model_validate(parsed_request["comparison_period"])
    comparison_type = comparison_type_for_periods(target, comparison)
    filters = [
        normalize_filter(FilterCondition.model_validate(item))
        for item in parsed_request.get("filters", [])
    ]
    return AnalysisRequest(
        metric=metric,
        target_period=f"{target.year:04d}-{target.month:02d}",
        comparison_period=f"{comparison.year:04d}-{comparison.month:02d}",
        comparison_type=comparison_type,  # type: ignore[arg-type]
        filters=filters,
        expected_direction=parsed_request.get("expected_direction", "unspecified"),
    )


def _action_signature(action_name: str, request: AnalysisRequest) -> str:
    filters = tuple(
        sorted(
            (item.dimension, item.value, item.operator)
            for item in request.filters
        )
    )
    return (
        f"{action_name}|{request.metric}|{request.target_period}|"
        f"{request.comparison_period}|{request.comparison_type}|"
        f"{request.expected_direction}|{filters}"
    )


def _blocked_observation(
    *,
    step: int,
    action_name: str,
    reason: str,
    dedupe_key: str,
) -> dict[str, object]:
    return {
        "step": step,
        "action_name": action_name,
        "status": "blocked",
        "reason": reason,
        "evidence": None,
        "error": reason,
        "tool_called": False,
        "dedupe_key": dedupe_key,
        "inspection_outcome": None,
    }


async def action_executor(
    state: RootCauseState,
    runtime: Runtime[DataAgentContext],
) -> dict[str, object]:
    """执行 Supervisor 选定的唯一 Action，并生成 Observation。"""
    observations = list(state.get("observations", []))
    decision = state.get("supervisor_decision") or {}
    action_name = decision.get("tool_name")
    step = len(observations) + 1

    if len(observations) >= MAX_ACTION_EXECUTIONS:
        return {
            "observations": observations,
            "stop_reason": "budget_exhausted",
        }

    if not action_name:
        obs = _blocked_observation(
            step=step,
            action_name="",
            reason="supervisor 未指定 Action",
            dedupe_key="",
        )
        return {
            "observations": observations + [obs],
            "stop_reason": "invalid_decision",
        }

    try:
        action = get_action(action_name)
    except ValueError as exc:
        obs = _blocked_observation(
            step=step,
            action_name=action_name,
            reason=f"未注册 Action：{action_name}",
            dedupe_key=action_name,
        )
        return {
            "observations": observations + [obs],
            "stop_reason": "invalid_action",
        }

    skill_result = state.get("skill_result") or {}
    skill = load_skill(skill_result.get("skill_name") or "")
    capabilities = resolve_skill_capabilities(skill)
    if action.capability not in capabilities["available_capabilities"]:
        obs = _blocked_observation(
            step=step,
            action_name=action_name,
            reason=f"Action 未授权：{action_name}",
            dedupe_key=action_name,
        )
        return {
            "observations": observations + [obs],
            "stop_reason": "unauthorized_action",
        }

    schema_result = state.get("schema_result") or {}
    selected_task_type = schema_result.get("task_type")
    compatible = (
        action_name == "change_inspection"
        and selected_task_type in {
            "period_comparison", "dimension_attribution", "metric_decomposition",
        }
    ) or (
        action_name == "dimension_attribution"
        and selected_task_type in {
            "period_comparison", "dimension_attribution", "metric_decomposition",
        }
    ) or (
        action_name == "metric_decomposition"
        and selected_task_type in {
            "period_comparison", "dimension_attribution", "metric_decomposition",
        }
    ) or (
        action_name == "business_diagnosis"
        and selected_task_type in {
            "period_comparison", "dimension_attribution", "metric_decomposition", "hypothesis_analysis",
        }
    )
    if not compatible:
        obs = _blocked_observation(
            step=step,
            action_name=action_name,
            reason=(
                f"任务类型 {schema_result.get('task_type')} 暂不支持 "
                f"Action {action_name}"
            ),
            dedupe_key=action_name,
        )
        return {
            "observations": observations + [obs],
            "stop_reason": "unsupported_task_type",
        }

    parsed_request = (state.get("parsed_result") or {}).get("parsed_request") or {}
    try:
        request = _build_analysis_request(parsed_request)
    except Exception as exc:
        obs = _blocked_observation(
            step=step,
            action_name=action_name,
            reason=f"参数校验失败：{exc}",
            dedupe_key=action_name,
        )
        return {
            "observations": observations + [obs],
            "stop_reason": "invalid_parameters",
        }

    signature = _action_signature(action_name, request)
    ready_outcome = None
    if action_name in {"dimension_attribution", "metric_decomposition", "business_diagnosis"}:
        inspection_signature = _action_signature("change_inspection", request)
        ready_observation = next((
            item for item in reversed(observations)
            if item.get("action_name") == "change_inspection"
            and item.get("status") == "ready"
            and item.get("dedupe_key") == inspection_signature
            and item.get("inspection_outcome")
        ), None)
        if ready_observation is None:
            obs = _blocked_observation(
                step=step, action_name=action_name,
                reason="专业分析必须先取得同一请求的 ChangeInspection.ready",
                dedupe_key=signature,
            )
            return {"observations": observations + [obs], "stop_reason": "verification_not_ready"}
        try:
            ready_outcome = InspectionOutcome.model_validate(ready_observation["inspection_outcome"])
            overall = ready_outcome.overall_result
            metric_context = ready_outcome.metric_context
            if (
                ready_outcome.status != "ready" or overall is None or metric_context is None
                or overall.target_period != request.target_period
                or overall.comparison_period != request.comparison_period
                or request.metric not in {metric_context.name, *metric_context.aliases}
            ):
                raise ValueError("ready 结果与当前请求不一致")
            request = request.model_copy(update={"request_id": ready_outcome.request_id})
        except Exception as exc:
            obs = _blocked_observation(
                step=step, action_name=action_name,
                reason=f"变化检验证据无效：{exc}", dedupe_key=signature,
            )
            return {"observations": observations + [obs], "stop_reason": "invalid_inspection_evidence"}
    prior_same = [item for item in observations if item.get("dedupe_key") == signature]
    if any(item.get("status") in {"ready", "completed", "partial", "unavailable"} for item in prior_same):
        obs = _blocked_observation(
            step=step,
            action_name=action_name,
            reason="相同参数已完成 Action，拒绝重复执行",
            dedupe_key=signature,
        )
        return {
            "observations": observations + [obs],
            "stop_reason": "duplicate_action",
        }
    if any(item.get("status") == "rejected" for item in prior_same):
        obs = _blocked_observation(
            step=step,
            action_name=action_name,
            reason="相同参数已被业务拒绝，不重试",
            dedupe_key=signature,
        )
        return {
            "observations": observations + [obs],
            "stop_reason": "duplicate_action",
        }
    failed_same = [item for item in prior_same if item.get("status") == "failed"]
    if len(failed_same) >= 2:
        obs = _blocked_observation(
            step=step,
            action_name=action_name,
            reason="相同参数技术失败已重试，拒绝再次执行",
            dedupe_key=signature,
        )
        return {
            "observations": observations + [obs],
            "stop_reason": "retry_exhausted",
        }

    try:
        if action_name == "dimension_attribution":
            assert ready_outcome is not None
            child_input = DimensionAttributionInput(
                raw_question=state["raw_question"], request=request,
                inspection_outcome=ready_outcome,
                metric_context=ready_outcome.metric_context,
                skill_name=skill.name, skill_version=skill.version,
                skill_strategy=skill.strategy,
                available_tool_names=tuple(ATTRIBUTION_TOOL_REGISTRY),
            )
            execution: AttributionExecution = await asyncio.wait_for(
                action.executor(child_input, runtime), timeout=_attribution_timeout(),
            )
            result = execution.result
            observation = {
                "step": step, "action_name": action_name,
                "status": (
                    "failed" if result.status == "failed" else
                    "completed" if result.completion_status == "complete" else
                    "partial" if result.completion_status == "partial" else "blocked"
                ),
                "reason": result.reason,
                "completion_status": result.completion_status,
                "attribution_stop_reason": result.stop_reason,
                "evidence": {"paths": [p.model_dump(mode="json") for p in result.paths],
                             "nodes": execution.nodes, "total_query_count": result.total_query_count},
                "error": None if result.status != "failed" else result.reason,
                "tool_called": True, "dedupe_key": signature,
                "inspection_outcome": None,
                "attribution_result": result.model_dump(mode="json"),
            }
        elif action_name == "metric_decomposition":
            assert ready_outcome is not None
            child_input = MetricDecompositionInput(
                raw_question=state["raw_question"],
                request=request,
                inspection_outcome=ready_outcome,
                metric_context=ready_outcome.metric_context,
                skill_name=skill.name,
                skill_version=skill.version,
                skill_strategy=skill.strategy,
            )
            execution: DecompositionExecution = await asyncio.wait_for(
                action.executor(child_input, runtime), timeout=_decomposition_timeout(),
            )
            result = execution.result
            observation = {
                "step": step, "action_name": action_name,
                "status": (
                    "failed" if result.status == "failed" else
                    "completed" if result.completion_status == "complete" else
                    "partial" if result.completion_status == "partial" else "blocked"
                ),
                "reason": result.reason,
                "completion_status": result.completion_status,
                "decomposition_stop_reason": result.stop_reason,
                "evidence": {
                    "formula": result.formula.model_dump(mode="json") if result.formula else None,
                    "observations": [item.model_dump(mode="json") for item in result.observations],
                    "components": [item.model_dump(mode="json") for item in result.components],
                    "reconciliation": result.reconciliation.model_dump(mode="json") if result.reconciliation else None,
                    "total_query_count": sum(
                        len(item.values.sql_by_period)
                        for item in result.observations
                        if item.values is not None
                    ),
                    "nodes": execution.nodes,
                },
                "error": None if result.status != "failed" else result.reason,
                "tool_called": True, "dedupe_key": signature,
                "inspection_outcome": None,
                "decomposition_result": result.model_dump(mode="json"),
            }
        elif action_name == "business_diagnosis":
            assert ready_outcome is not None
            attribution_result = None
            decomposition_result = None
            for prior in observations:
                if prior.get("action_name") == "dimension_attribution" and prior.get("attribution_result"):
                    from app.agent.anomaly_agent.DimensionAttribution_Agent.schemas import AttributionResult
                    attribution_result = AttributionResult.model_validate(prior["attribution_result"])
                if prior.get("action_name") == "metric_decomposition" and prior.get("decomposition_result"):
                    from app.agent.anomaly_agent.MetricDecomposition_Agent.schemas import DecompositionResult
                    decomposition_result = DecompositionResult.model_validate(prior["decomposition_result"])
            child_input = BusinessDiagnosisInput(
                raw_question=state["raw_question"], analysis_request=request,
                inspection_outcome=ready_outcome, attribution_result=attribution_result,
                decomposition_result=decomposition_result,
                metric_context=ready_outcome.metric_context,
                skill_strategy=skill.strategy,
                available_tool_names=("scoped_metric_decomposition",),
            )
            execution: BusinessDiagnosisExecution = await asyncio.wait_for(
                action.executor(child_input, runtime), timeout=_business_diagnosis_timeout(),
            )
            result = execution.result
            observation = {
                "step": step, "action_name": action_name,
                "status": (
                    "failed" if result.status == "failed" else
                    "completed" if result.completion_status == "complete" else
                    "partial" if result.completion_status == "partial" else "unavailable"
                ),
                "reason": result.summary,
                "completion_status": result.completion_status,
                "business_diagnosis_stop_reason": result.stop_reason,
                "evidence": {
                    "findings": [item.model_dump(mode="json") for item in result.findings],
                    "hypotheses": [item.model_dump(mode="json") for item in result.hypotheses],
                    "evidence_ids": result.evidence_ids, "nodes": execution.nodes,
                    "total_query_count": result.total_query_count,
                },
                "error": None if result.status != "failed" else result.summary,
                "tool_called": True, "dedupe_key": signature,
                "inspection_outcome": None,
                "business_diagnosis_result": result.model_dump(mode="json"),
            }
        else:
            outcome = await asyncio.wait_for(
                action.executor(request, runtime), timeout=ACTION_TIMEOUT_SECONDS,
            )
            observation = {
                "step": step, "action_name": action_name,
                "status": outcome.status, "reason": outcome.reason,
                "evidence": _evidence(outcome), "error": None,
                "tool_called": True, "dedupe_key": signature,
                "inspection_outcome": outcome.model_dump(mode="json"),
            }
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        observation = {
            "step": step,
            "action_name": action_name,
            "status": "failed",
            "reason": "tool_timeout",
            "evidence": None,
            "error": "Tool 执行超时",
            "tool_called": True,
            "dedupe_key": signature,
            "inspection_outcome": None,
        }
    except Exception as exc:
        observation = {
            "step": step,
            "action_name": action_name,
            "status": "failed",
            "reason": "tool_exception",
            "evidence": None,
            "error": str(exc),
            "tool_called": True,
            "dedupe_key": signature,
            "inspection_outcome": None,
        }

    new_observations = observations + [observation]
    stop_reason: str | None = None
    if observation["status"] == "rejected":
        stop_reason = "rejected"
    if len(new_observations) >= MAX_ACTION_EXECUTIONS:
        if action_name == "business_diagnosis" and observation["status"] == "completed":
            stop_reason = stop_reason or "completed"
        elif action_name == "business_diagnosis" and observation["status"] == "partial":
            stop_reason = stop_reason or "partial_completion"
        else:
            stop_reason = stop_reason or "budget_exhausted"

    output: dict[str, object] = {"observations": new_observations}
    if stop_reason is not None:
        output["stop_reason"] = stop_reason
    return output


def _evidence(outcome: InspectionOutcome) -> dict[str, Any]:
    overall = outcome.overall_result
    return {
        "metric": outcome.metric_context.name if outcome.metric_context else None,
        "metric_key": outcome.metric_context.metric_key if outcome.metric_context else None,
        "target_period": overall.target_period if overall else None,
        "comparison_period": overall.comparison_period if overall else None,
        "current_value": str(overall.current_value) if overall and overall.current_value is not None else None,
        "comparison_value": str(overall.comparison_value) if overall and overall.comparison_value is not None else None,
        "change_amount": str(overall.change_amount) if overall and overall.change_amount is not None else None,
        "change_rate": str(overall.change_rate) if overall and overall.change_rate is not None else None,
    }
