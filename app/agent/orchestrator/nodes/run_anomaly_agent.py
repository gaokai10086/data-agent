"""Orchestrator 与异动归因子 Agent 之间的适配层。

意图识别和分流已经在 Orchestrator 完成；本文件不再次分类，也不解析 SQL。
它只转换输入 State、调用子 Graph、转发可供前端消费的事件。
"""

from __future__ import annotations

from langgraph.runtime import Runtime

from app.agent.anomaly_agent.graph import anomaly_graph
from app.agent.data_agent.context import DataAgentContext
from app.agent.orchestrator.state import OrchestratorState


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
    result = await anomaly_graph.ainvoke(
        {
            "raw_question": state.get("rewritten_query") or state.get("query", ""),
            "intent": state.get("intent"),
        },
        context=runtime.context,
    )
    # 前置 Agent 的结构化结果先对外暴露，便于 UI 区分“已核验但未进入归因”
    # 与“已完成归因报告”。
    preparation = result.get("preparation_result")
    runtime.stream_writer({
        "type": "anomaly_preparation_result",
        "agent": "anomaly_preparation",
        **(preparation.model_dump(mode="json") if preparation is not None else {
            "status": "failed", "reason": "missing_preparation_result",
            "user_message": "异动分析前置 Agent 未返回结构化结果。",
        }),
    })
    request = result["analysis_request"]
    # 结构化事件便于调用方检查指标、目标月份和对比月份。
    # 它不是 anomaly_result；前端不能把本阶段误显示成“归因完成”。
    runtime.stream_writer({
        "type": "anomaly_request",
        "agent": "anomaly",
        **request.model_dump(),
    })
    verification = result["change_verification"]
    runtime.stream_writer({
        "type": "change_verification",
        "agent": "anomaly",
        **verification.model_dump(mode="json"),
    })
    strategy = result.get("analysis_strategy")
    if strategy is not None:
        runtime.stream_writer({
            "type": "analysis_strategy", "agent": "anomaly",
            **strategy.model_dump(mode="json"),
        })
    report = result.get("change_analysis_report")
    if report is not None:
        runtime.stream_writer({"type": "change_analysis_report", "agent": "anomaly",
                               **report.model_dump(mode="json")})
    # 使用 token 兼容现有 UI；不冒用旧 anomaly_result 报告卡片协议。
    runtime.stream_writer({
        "type": "token",
        "content": (report.summary if report is not None else verification.summary)
        + " ".join(verification.warnings),
    })
    return {"agent_result": result}
