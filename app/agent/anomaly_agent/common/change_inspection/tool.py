"""可复用的确定性变化检验 Tool。

按固定顺序完成：加载指标 Metadata -> 总体周期查询 -> 确定性变化核验，
不引入 LLM 规划或 ReAct 循环。
"""

from __future__ import annotations

from langgraph.runtime import Runtime

from app.agent.anomaly_agent.common.change_inspection.nodes.load_metric_context import (
    resolve_metric_context,
)
from app.agent.anomaly_agent.common.change_inspection.nodes.verify_change import assess_change
from app.agent.anomaly_agent.common.change_inspection.tools.registry import get_anomaly_handler
from app.agent.anomaly_agent.schemas import (
    AnalysisRequest,
    ChangeVerification,
    InspectionOutcome,
    MetricContext,
    OverallComparisonResult,
)
from app.agent.data_agent.context import DataAgentContext
from app.core.logging import logger


GENERIC_FAILURE_MESSAGE = "变化检验暂时无法完成，请稍后重试。"


def _has_ungrounded_filter(request: AnalysisRequest) -> bool:
    return any(item.dimension is None for item in request.filters)


def _failed(
    request_id: str | None,
    reason: str,
) -> InspectionOutcome:
    return InspectionOutcome(
        status="failed",
        reason=reason,
        request_id=request_id,
        user_message=GENERIC_FAILURE_MESSAGE,
    )


async def verify_metric_change(
    request: AnalysisRequest,
    runtime: Runtime[DataAgentContext],
) -> InspectionOutcome:
    """检验用户提出的周期变化前提是否得到真实数据支持。"""

    if _has_ungrounded_filter(request):
        return InspectionOutcome(
            status="rejected",
            reason="ungrounded_filter",
            request_id=request.request_id,
            user_message="查询条件尚未完成字段定位，请先明确筛选字段后重试。",
        )

    metric_context: MetricContext | None = None
    overall_result: OverallComparisonResult | None = None
    verification: ChangeVerification | None = None

    try:
        metric_context = await resolve_metric_context(request, runtime)
    except Exception as exc:
        logger.exception(
            "变化检验加载指标 Metadata 失败: request_id=%s metric=%s",
            request.request_id,
            request.metric,
        )
        return _failed(request.request_id, "metric_context_load_failed")

    try:
        handler = get_anomaly_handler("overall_comparison")
        result = await handler.compare(request, metric_context, runtime)
        if not isinstance(result, OverallComparisonResult):
            raise TypeError("overall_comparison handler 返回了非总体对比结果")
        overall_result = result
    except Exception as exc:
        logger.exception(
            "变化检验总体查询失败: request_id=%s metric=%s",
            request.request_id,
            request.metric,
        )
        return _failed(request.request_id, "overall_query_failed")

    try:
        verification = assess_change(request, metric_context, overall_result)
    except Exception as exc:
        logger.exception(
            "变化检验核验失败: request_id=%s metric=%s",
            request.request_id,
            request.metric,
        )
        return _failed(request.request_id, "verification_failed")

    if verification.decision == "continue":
        return InspectionOutcome(
            status="ready",
            reason=verification.reason,
            request_id=request.request_id,
            user_message=verification.summary,
            metric_context=metric_context,
            overall_result=overall_result,
            verification=verification,
        )

    return InspectionOutcome(
        status="rejected",
        reason=verification.reason,
        request_id=request.request_id,
        user_message=verification.summary,
        metric_context=metric_context,
        overall_result=overall_result,
        verification=verification,
    )
