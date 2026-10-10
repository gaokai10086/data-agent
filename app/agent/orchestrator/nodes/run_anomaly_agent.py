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
    当前阶段不查询数据库，也不生成归因报告。
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
        {"raw_question": state.get("query", "")},
        context=runtime.context,
    )
    request = result["analysis_request"]
    # 结构化事件便于调用方检查指标、目标月份和对比月份。
    # 它不是 anomaly_result；前端不能把本阶段误显示成“归因完成”。
    runtime.stream_writer({
        "type": "anomaly_request",
        "agent": "anomaly",
        **request.model_dump(),
    })
    # 展示文案属于事件层，不写入子 Graph State。
    runtime.stream_writer({
        "type": "token",
        "content": (
            f"已解析：指标={request.metric}，目标月份={request.target_period}，"
            f"对比月份={request.comparison_period}，比较类型={request.comparison_type}，"
            f"筛选条件={[(item.dimension, item.value) for item in request.filters]}。"
            "当前尚未查询数据或验证下降。"
        ),
    })
    return {"agent_result": result}
