"""AnomalyPreparationAgent 图。"""

from langgraph.constants import END, START
from langgraph.graph import StateGraph

from app.agent.anomaly_agent.Change_Inspection_Agent.nodes.load_metric_context import load_metric_context
from app.agent.anomaly_agent.Change_Inspection_Agent.nodes.query_overall import query_overall
from app.agent.anomaly_agent.Change_Inspection_Agent.nodes.verify_change import verify_change
from app.agent.anomaly_agent.Change_Inspection_Agent.state import PreparationState
from app.agent.anomaly_agent.schemas import PreparationResult
from app.agent.data_agent.context import DataAgentContext


def mark_preparation_result(state: PreparationState) -> dict[str, object]:
    verification = state.get("change_verification")
    request = state.get("analysis_request")
    if verification is None:
        result = PreparationResult(
            status="failed", reason="missing_verification",
            request_id=request.request_id if request else None,
            user_message="总体变化核验结果缺失，无法继续异动分析。",
        )
    else:
        result = PreparationResult(
            status="ready" if verification.decision == "continue" else "rejected",
            reason=verification.reason,
            request_id=request.request_id if request else None,
            user_message=verification.summary,
        )
    return {"preparation_result": result}


def build_preparation_graph(*, checkpointer=None):
    builder = StateGraph(PreparationState, context_schema=DataAgentContext)
    builder.add_node("load_metric_context", load_metric_context)
    builder.add_node("query_overall", query_overall)
    builder.add_node("verify_change", verify_change)
    builder.add_node("mark_preparation_result", mark_preparation_result)
    builder.add_edge(START, "load_metric_context")
    builder.add_edge("load_metric_context", "query_overall")
    builder.add_edge("query_overall", "verify_change")
    builder.add_edge("verify_change", "mark_preparation_result")
    builder.add_edge("mark_preparation_result", END)
    return builder.compile(checkpointer=checkpointer)


anomaly_preparation_agent = build_preparation_graph()
