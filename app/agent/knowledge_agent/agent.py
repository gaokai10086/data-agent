"""知识解释分支的预留适配节点。

当前阶段暂时复用通用直接回答能力，先保留独立的路由边界。后续接入
指标定义库、业务术语库或知识检索后，只需要替换本节点，不影响其他 Agent。
"""
from __future__ import annotations

from langgraph.runtime import Runtime

from app.agent.data_agent.context import DataAgentContext
from app.agent.direct_answer.agent import run_direct_answer
from app.agent.orchestrator.state import OrchestratorState


async def run_knowledge_agent(
    state: OrchestratorState,
    runtime: Runtime[DataAgentContext],
) -> dict[str, object]:
    runtime.stream_writer({"stage": "进入知识解释 Agent（预留）"})
    return await run_direct_answer(state, runtime)
