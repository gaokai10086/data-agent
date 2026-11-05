"""总体指标查询节点。"""

from __future__ import annotations

from langgraph.runtime import Runtime

from app.agent.anomaly_agent.Change_Inspection_Agent.tools.registry import get_anomaly_handler
from app.agent.anomaly_agent.Change_Inspection_Agent.state import PreparationState
from app.agent.data_agent.context import DataAgentContext


async def query_overall(
    state: PreparationState,
    runtime: Runtime[DataAgentContext],
) -> dict[str, object]:
    """通过注册表选择处理器，并写回 Graph State。"""

    plan = state.get("analysis_plan")
    analysis_type = plan.analysis_type if plan is not None else "overall_comparison"
    handler = get_anomaly_handler(analysis_type)
    result = await handler.run(state, runtime)
    return {"analysis_result": result}
