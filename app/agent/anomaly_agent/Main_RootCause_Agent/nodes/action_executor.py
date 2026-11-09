"""Action Executor：鉴权、参数构造、去重、超时与 Observation 生成。"""

from __future__ import annotations

import asyncio
from typing import Any

from langgraph.runtime import Runtime

from app.agent.anomaly_agent.Main_RootCause_Agent.actions import (
    ACTION_REGISTRY,
    MAX_ACTION_EXECUTIONS,
    get_action,
)
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
    if schema_result.get("task_type") != action.task_type:
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
    prior_same = [item for item in observations if item.get("dedupe_key") == signature]
    if any(item.get("status") == "ready" for item in prior_same):
        obs = _blocked_observation(
            step=step,
            action_name=action_name,
            reason="相同参数已完成变化检验，拒绝重复执行",
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
        outcome = await asyncio.wait_for(
            action.executor(request, runtime),
            timeout=ACTION_TIMEOUT_SECONDS,
        )
        observation: dict[str, object] = {
            "step": step,
            "action_name": action_name,
            "status": outcome.status,
            "reason": outcome.reason,
            "evidence": _evidence(outcome),
            "error": None,
            "tool_called": True,
            "dedupe_key": signature,
            "inspection_outcome": outcome.model_dump(mode="json"),
        }
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
        stop_reason = stop_reason or "budget_exhausted"

    output: dict[str, object] = {"observations": new_observations}
    if stop_reason is not None:
        output["stop_reason"] = stop_reason
    return output


def _evidence(outcome: InspectionOutcome) -> dict[str, Any]:
    overall = outcome.overall_result
    return {
        "metric_key": outcome.metric_context.metric_key if outcome.metric_context else None,
        "target_period": overall.target_period if overall else None,
        "comparison_period": overall.comparison_period if overall else None,
        "current_value": str(overall.current_value) if overall and overall.current_value is not None else None,
        "comparison_value": str(overall.comparison_value) if overall and overall.comparison_value is not None else None,
        "change_amount": str(overall.change_amount) if overall and overall.change_amount is not None else None,
        "change_rate": str(overall.change_rate) if overall and overall.change_rate is not None else None,
    }
