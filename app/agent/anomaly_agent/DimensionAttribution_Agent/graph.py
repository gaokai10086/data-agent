"""独立 DimensionAttributionAgent Graph。"""

from langgraph.constants import END, START
from langgraph.graph import StateGraph

from app.agent.anomaly_agent.DimensionAttribution_Agent.nodes import (
    execute_attribution_tools,
    finalize_attribution,
    main_reason,
)
from app.agent.anomaly_agent.DimensionAttribution_Agent.state import DimensionAttributionState
from app.agent.data_agent.context import DataAgentContext


def route_after_reason(state: DimensionAttributionState) -> str:
    decision = state.get("decision")
    return "execute_attribution_tools" if decision and decision.action == "execute_tool" else "finalize_attribution"


def route_after_tools(state: DimensionAttributionState) -> str:
    if state.get("stop_reason") or state.get("query_count", 0) >= state["input"].skill_strategy.max_query_budget:
        return "finalize_attribution"
    return "main_reason"


def build_dimension_attribution_graph():
    builder = StateGraph(DimensionAttributionState, context_schema=DataAgentContext)
    builder.add_node("main_reason", main_reason)
    builder.add_node("execute_attribution_tools", execute_attribution_tools)
    builder.add_node("finalize_attribution", finalize_attribution)
    builder.add_edge(START, "main_reason")
    builder.add_conditional_edges(
        "main_reason",
        route_after_reason,
        {"execute_attribution_tools": "execute_attribution_tools", "finalize_attribution": "finalize_attribution"},
    )
    builder.add_conditional_edges(
        "execute_attribution_tools",
        route_after_tools,
        {"main_reason": "main_reason", "finalize_attribution": "finalize_attribution"},
    )
    builder.add_edge("finalize_attribution", END)
    return builder.compile()


dimension_attribution_graph = build_dimension_attribution_graph()
