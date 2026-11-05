"""Orchestrator 与异动归因子 Agent 之间的适配层。

意图识别和分流已经在 Orchestrator 完成；本文件不再次分类，也不解析 SQL。
它只转换输入 State、调用子 Graph、转发可供前端消费的事件。
"""

from __future__ import annotations

from langgraph.runtime import Runtime

from app.agent.anomaly_agent.graph import anomaly_graph
from app.agent.anomaly_agent.schemas import (
    AnalysisRequest,
    ChangeVerification,
    PreparationResult,
)
from app.agent.data_agent.context import DataAgentContext
from app.agent.orchestrator.state import OrchestratorState
from app.core.logging import logger


TECHNICAL_FAILURE_MESSAGE = "异动分析暂时无法完成，请稍后重试。"


def _technical_failure(
    runtime: Runtime[DataAgentContext],
    reason: str,
    *,
    request_id: str | None = None,
) -> dict[str, object]:
    """返回结构化技术失败，并且只发送一次用户可见的最终错误提示。"""
    failure = PreparationResult(
        status="failed",
        reason=reason,
        request_id=request_id,
        user_message=TECHNICAL_FAILURE_MESSAGE,
    )
    runtime.stream_writer({
        "type": "error_response",
        "agent": "anomaly",
        "content": TECHNICAL_FAILURE_MESSAGE,
        "error": TECHNICAL_FAILURE_MESSAGE,
    })
    return {"agent_result": {"preparation_result": failure}}


async def run_anomaly_agent(
    state: OrchestratorState,
    runtime: Runtime[DataAgentContext],
) -> dict[str, object]:
    """把 Orchestrator 的 query 交给异动 Graph，并返回其最终状态。

    runtime.stream_writer 写出的事件会进入上层 SSE；最终返回值则进入
    OrchestratorState.agent_result，供非流式调用或后续编排使用。
    总体核实后可进入有限维度分析，输出阶段性证据报告。
    """
    # 先告诉前端已经进入异动分支；这是进度提示，不代表解析成功。
    runtime.stream_writer({
        "type": "progress",
        "stage": "anomaly",
        "agent": "anomaly",
        "content": "已分流到异动归因 Agent，正在解析指标和比较月份",
    })
    # 两个 Agent 的 State 不共享：这里显式把 query 映射为子 Graph 的 raw_question。
    try:
        result = await anomaly_graph.ainvoke(
            {
                "raw_question": state.get("rewritten_query") or state.get("query", ""),
                "intent": state.get("intent"),
            },
            context=runtime.context,
        )
    except Exception as exc:
        logger.exception("异动分析子图执行失败: {}", exc)
        return _technical_failure(runtime, "anomaly_execution_error")

    if not isinstance(result, dict):
        logger.error("异动分析子图返回了非对象结果")
        return _technical_failure(runtime, "invalid_anomaly_result")

    # 前置 Agent 的结构化结果先对外暴露，便于 UI 区分“已核验但未进入归因”
    # 与“已完成归因报告”。
    preparation = result.get("preparation_result")
    if not isinstance(preparation, PreparationResult):
        logger.error("异动分析结果缺少 preparation_result")
        return _technical_failure(runtime, "missing_preparation_result")
    if preparation.status == "failed":
        logger.error("异动分析前置阶段失败: {}", preparation.reason)
        return _technical_failure(
            runtime,
            preparation.reason or "preparation_failed",
            request_id=preparation.request_id,
        )

    request = result.get("analysis_request")
    if not isinstance(request, AnalysisRequest):
        logger.error("异动分析结果缺少 analysis_request")
        return _technical_failure(
            runtime,
            "missing_analysis_request",
            request_id=preparation.request_id,
        )

    verification = result.get("change_verification")
    if not isinstance(verification, ChangeVerification):
        logger.error("异动分析结果缺少 change_verification")
        return _technical_failure(
            runtime,
            "missing_change_verification",
            request_id=request.request_id,
        )

    runtime.stream_writer({
        "type": "anomaly_preparation_result",
        "agent": "anomaly_preparation",
        **preparation.model_dump(mode="json"),
    })
    # 结构化事件便于调用方检查指标、目标月份和对比月份。
    # 它不是 anomaly_result；前端不能把本阶段误显示成“归因完成”。
    runtime.stream_writer({
        "type": "anomaly_request",
        "agent": "anomaly",
        **request.model_dump(),
    })
    runtime.stream_writer({
        "type": "change_verification",
        "agent": "anomaly",
        **verification.model_dump(mode="json"),
    })

    strategy = result.get("analysis_strategy") if preparation.status == "ready" else None
    report = result.get("change_analysis_report") if preparation.status == "ready" else None
    if strategy is not None:
        runtime.stream_writer({
            "type": "analysis_strategy",
            "agent": "anomaly",
            **strategy.model_dump(mode="json"),
        })
    if report is not None:
        runtime.stream_writer({
            "type": "change_analysis_report",
            "agent": "anomaly",
            **report.model_dump(mode="json"),
        })
    # 使用 token 兼容现有 UI；不冒用旧 anomaly_result 报告卡片协议。
    runtime.stream_writer({
        "type": "token",
        "content": (report.summary if report is not None else verification.summary)
        + " ".join(verification.warnings),
    })
    return {"agent_result": result}
