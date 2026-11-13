"""独立 BusinessDiagnosisAgent Graph。"""

from langgraph.constants import END, START
from langgraph.graph import StateGraph

from app.agent.anomaly_agent.BusinessDiagnosis_Agent.nodes import (
    execute_diagnosis_tools,
    finalize_diagnosis,
    main_reason,
    prepare_evidence,
)
from app.agent.anomaly_agent.BusinessDiagnosis_Agent.state import BusinessDiagnosisState
from app.agent.data_agent.context import DataAgentContext


def route_after_reason(state: BusinessDiagnosisState) -> str:
    decision = state.get("decision")
    if decision and decision.action == "execute_tool":
        return "execute_diagnosis_tools"
    return "finalize_diagnosis"


def route_after_tools(state: BusinessDiagnosisState) -> str:
    last = (state.get("observations") or [])[-1:] 
    if last and last[0].status == "ok":
        return "main_reason"
    return "finalize_diagnosis"


def build_business_diagnosis_graph():
    builder = StateGraph(BusinessDiagnosisState, context_schema=DataAgentContext)
    builder.add_node("prepare_evidence", prepare_evidence)
    builder.add_node("main_reason", main_reason)
    builder.add_node("execute_diagnosis_tools", execute_diagnosis_tools)
    builder.add_node("finalize_diagnosis", finalize_diagnosis)

    builder.add_edge(START, "prepare_evidence")
    builder.add_edge("prepare_evidence", "main_reason")
    builder.add_conditional_edges(
        "main_reason", route_after_reason,
        {"execute_diagnosis_tools": "execute_diagnosis_tools", "finalize_diagnosis": "finalize_diagnosis"},
    )
    builder.add_conditional_edges(
        "execute_diagnosis_tools", route_after_tools,
        {"main_reason": "main_reason", "finalize_diagnosis": "finalize_diagnosis"},
    )
    builder.add_edge("finalize_diagnosis", END)
    return builder.compile()


business_diagnosis_graph = build_business_diagnosis_graph()
