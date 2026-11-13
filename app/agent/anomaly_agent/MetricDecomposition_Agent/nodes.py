"""MetricDecompositionAgent 的确定性节点。"""

from app.agent.anomaly_agent.MetricDecomposition_Agent.schemas import (
    DecompositionObservation,
    DecompositionResult,
)
from app.agent.anomaly_agent.MetricDecomposition_Agent.state import MetricDecompositionState
from app.agent.anomaly_agent.MetricDecomposition_Agent.tools import DecompositionQueryError, MetricDecompositionTool


async def main_reason(state: MetricDecompositionState, runtime=None) -> dict:
    input_data = state["input"]
    base = {"request_id": input_data.request.request_id, "formula": input_data.metric_context.decomposition}
    if input_data.inspection_outcome.status != "ready":
        return {**base, "stop_reason": "verification_not_ready"}
    if input_data.metric_context.decomposition is None:
        return {**base, "stop_reason": "formula_unavailable"}
    formula = input_data.metric_context.decomposition
    count_factor, average_factor = formula.factors
    if average_factor.source.numerator_metric_key != input_data.metric_context.metric_key:
        return {**base, "stop_reason": "formula_mismatch"}
    return {**base, "stop_reason": "execute_decomposition"}


async def execute_decomposition_tools(state: MetricDecompositionState, runtime=None) -> dict:
    if state.get("stop_reason") != "execute_decomposition":
        return {}
    try:
        payload = await MetricDecompositionTool().run(state["input"], runtime)
        observations = [
            DecompositionObservation(
                factor_key=value.factor_key, metric_key=value.metric_key,
                status="ok", values=value,
            ) for value in payload["observations"]
        ]
        return {
            "observations": observations,
            "components": payload["components"],
            "reconciliation": payload["reconciliation"],
            "formula": payload["formula"],
            "stop_reason": "completed",
        }
    except Exception as exc:
        message = str(exc)
        stop_reason = "execution_failed"
        if "依赖指标" in message:
            stop_reason = "dependency_unavailable"
        elif "零基数" in message:
            stop_reason = "zero_denominator"
        elif "未对账" in message:
            stop_reason = "reconciliation_failed"
        elif "公式" in message or "分子" in message:
            stop_reason = "formula_mismatch"
        observation = DecompositionObservation(
            factor_key="unknown", metric_key="unknown", status="failed", error=str(exc),
        )
        return {"observations": [observation], "stop_reason": stop_reason}


def finalize_decomposition(state: MetricDecompositionState) -> dict:
    stop_reason = state.get("stop_reason", "execution_failed")
    observations = state.get("observations", [])
    if stop_reason == "completed":
        result = DecompositionResult(
            request_id=state["request_id"], status="completed", completion_status="complete",
            reason="completed", stop_reason=stop_reason, formula=state.get("formula"),
            observations=observations, components=state.get("components", []),
            reconciliation=state.get("reconciliation"), summary="指标拆解完成，已返回真实依赖指标和确定性贡献证据。",
        )
    elif stop_reason in {
        "verification_not_ready", "formula_unavailable", "formula_mismatch",
        "dependency_unavailable", "zero_denominator", "reconciliation_failed",
    }:
        result = DecompositionResult(
            request_id=state["request_id"], status="unavailable", completion_status="none",
            reason=stop_reason, stop_reason=stop_reason, formula=state.get("formula"),
            observations=observations, summary="当前请求没有可执行的已发布指标拆解。",
        )
    else:
        result = DecompositionResult(
            request_id=state["request_id"], status="failed", completion_status="none",
            reason="execution_failed", stop_reason=stop_reason, formula=state.get("formula"),
            observations=observations, summary="指标拆解执行失败，未返回完整贡献结论。",
        )
    return {"result": result}

