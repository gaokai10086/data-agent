"""调用独立的智能问数子 Agent。"""
from __future__ import annotations

from langgraph.runtime import Runtime

from app.agent.data_agent.context import DataAgentContext
from app.agent.data_agent.graph import graph as data_agent_graph
from app.agent.data_agent.state import DataAgentState
from app.agent.orchestrator.state import OrchestratorState


async def run_data_agent(
    state: OrchestratorState,
    runtime: Runtime[DataAgentContext],
) -> dict[str, object]:
    """调用智能问数子 Agent，并把子图事件转发给上层 SSE。

    直接调用 ``ainvoke`` 只能拿到最终状态，子图节点通过
    ``stream_writer`` 写出的 ``bi_report``、阶段进度等事件不会自动
    穿透到前端。因此这里同时订阅 ``custom`` 和 ``values`` 两种流：

    - ``custom``：原样转发给 Orchestrator，再由 API 转成 SSE；
    - ``values``：保留子 Agent 的最终状态，供 Orchestrator 返回。
    """
    runtime.stream_writer({"stage": "进入智能问数 Agent", "agent": "data_agent"})

    result: dict[str, object] = {}
    async for mode, chunk in data_agent_graph.astream(
        input=DataAgentState(query=state.get("query", "")),
        context=runtime.context,
        stream_mode=["custom", "values"],
    ):
        if mode == "custom":
            # 关键适配：转发子图的 bi_report、thought、stage 等事件。
            runtime.stream_writer(chunk)
        elif mode == "values":
            result = chunk

    return {"agent_result": result}
