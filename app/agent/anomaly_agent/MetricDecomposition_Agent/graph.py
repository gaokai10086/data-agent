"""独立 MetricDecompositionAgent Graph。"""

from langgraph.constants import END, START
from langgraph.graph import StateGraph

from app.agent.anomaly_agent.MetricDecomposition_Agent.nodes import (
    execute_decomposition_tools,
    finalize_decomposition,
    main_reason,
)
from app.agent.anomaly_agent.MetricDecomposition_Agent.state import MetricDecompositionState
from app.agent.data_agent.context import DataAgentContext


def route_after_reason(state: MetricDecompositionState) -> str:
    return "execute_decomposition_tools" if state.get("stop_reason") == "execute_decomposition" else "finalize_decomposition"


def build_metric_decomposition_graph():
    builder = StateGraph(MetricDecompositionState, context_schema=DataAgentContext)
    builder.add_node("main_reason", main_reason)
    builder.add_node("execute_decomposition_tools", execute_decomposition_tools)
    builder.add_node("finalize_decomposition", finalize_decomposition)
    builder.add_edge(START, "main_reason")
    builder.add_conditional_edges(
        "main_reason", route_after_reason,
        {"execute_decomposition_tools": "execute_decomposition_tools", "finalize_decomposition": "finalize_decomposition"},
    )
    builder.add_edge("execute_decomposition_tools", "finalize_decomposition")
    builder.add_edge("finalize_decomposition", END)
    return builder.compile()


metric_decomposition_graph = build_metric_decomposition_graph()

