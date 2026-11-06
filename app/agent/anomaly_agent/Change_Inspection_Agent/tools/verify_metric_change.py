"""可复用的确定性总体变化检验服务。

输入为 Main Agent 已解析的 AnalysisRequest；工具内部使用临时字典向现有
Metadata 加载和总体查询 Handler 传递数据，不需要独立 LangGraph State。
本模块不调用 LLM 做核验决策，也不负责异动归因。
"""

from __future__ import annotations

from langgraph.runtime import Runtime

from app.agent.anomaly_agent.Change_Inspection_Agent.nodes.load_metric_context import (
    load_metric_context,
)
from app.agent.anomaly_agent.Change_Inspection_Agent.nodes.verify_change import (
    assess_change,
)
from app.agent.anomaly_agent.Change_Inspection_Agent.tools.registry import (
    get_anomaly_handler,
)
from app.agent.anomaly_agent.schemas import (
    AnalysisRequest,
    InspectionOutcome,
    MetricContext,
    OverallComparisonResult,
)
from app.agent.data_agent.context import DataAgentContext
from app.core.logging import logger


async def verify_metric_change(
    request: AnalysisRequest,
    runtime: Runtime[DataAgentContext],
) -> InspectionOutcome:
    """读取指标、查询总体值，并核实请求所描述的变化。

    返回 ready/rejected/failed：ready 只表示已完成总体变化核验；
    本工具不保证底层 SQL 口径或 ETL 数据水位已被独立验证。
    技术失败不会被误判为业务 rejected。
    """
    if not isinstance(request, AnalysisRequest):
        raise TypeError("verify_metric_change 需要有效的 AnalysisRequest")

    try:
        # 使用已有节点中的业务实现，但不运行旧子 Graph。
        loaded = await load_metric_context({"analysis_request": request}, runtime)
        metric = loaded.get("metric_context")
        if not isinstance(metric, MetricContext):
            raise TypeError("加载指标 Metadata 未返回 MetricContext")

        handler = get_anomaly_handler("overall_comparison")
        overall = await handler.run(
            {"analysis_request": request, "metric_context": metric},
            runtime,
        )
        if not isinstance(overall, OverallComparisonResult):
            raise TypeError("总体查询未返回 OverallComparisonResult")

        verification = assess_change(request, metric, overall)
        status = "ready" if verification.decision == "continue" else "rejected"
        return InspectionOutcome(
            status=status,
            reason=verification.reason,
            request_id=request.request_id,
            user_message=verification.summary,
            metric_context=metric,
            overall_result=overall,
            verification=verification,
        )
    except Exception as exc:
        logger.exception("总体变化检验工具执行失败: {}", exc)
        return InspectionOutcome(
            status="failed",
            reason="inspection_execution_error",
            request_id=request.request_id,
            user_message="变化检验暂时无法完成，请稍后重试。",
        )
