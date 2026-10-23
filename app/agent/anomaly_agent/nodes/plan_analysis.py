"""组装临时上下文、选择分析 Skill，并保存经过校验的策略。"""
from langgraph.runtime import Runtime

from app.agent.anomaly_agent.schemas import AnalysisContext
from app.agent.anomaly_agent.skills.registry import build_analysis_strategy
from app.agent.anomaly_agent.state import AnomalyState
from app.agent.data_agent.context import DataAgentContext


def route_after_verification(state: AnomalyState) -> str:
    return "plan_analysis" if state["change_verification"].decision == "continue" else "end"


async def plan_analysis(state: AnomalyState, runtime: Runtime[DataAgentContext]) -> dict[str, object]:
    context = AnalysisContext(
        request=state["analysis_request"], metric=state["metric_context"],
        comparison=state["analysis_result"], verification=state["change_verification"],
    )
    return {"analysis_strategy": await build_analysis_strategy(context)}
