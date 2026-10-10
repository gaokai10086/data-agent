"""汇总图表与洞察结果，保证 present_result 只执行一次。"""
from typing import Any, Dict

from langgraph.runtime import Runtime

from app.agent.data_agent.context import DataAgentContext
from app.agent.data_agent.state import DataAgentState


async def join_final_outputs(
    state: DataAgentState,
    runtime: Runtime[DataAgentContext],
) -> Dict[str, Any]:
    writer = getattr(runtime, "stream_writer", None)
    if writer:
        writer({"stage": "汇总输出结果"})
    return {}
