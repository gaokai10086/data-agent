"""应用级 Orchestrator Graph。

它只负责意图识别和子 Agent 分流，不实现 SQL 或异动归因逻辑。
"""
from langgraph.constants import END, START
from langgraph.graph import StateGraph

from app.agent.data_agent.context import DataAgentContext
from app.agent.orchestrator.nodes.intent_detection import intent_detection
from app.agent.orchestrator.nodes.rewrite_question import rewrite_question
from app.agent.orchestrator.nodes.run_anomaly_agent import run_anomaly_agent
from app.agent.orchestrator.nodes.run_data_agent import run_data_agent
from app.agent.orchestrator.nodes.run_direct_answer import run_direct_answer
from app.agent.orchestrator.state import OrchestratorState


def route_intent(state: OrchestratorState) -> str:
    """将 LLM 标签映射到 Orchestrator 的目标节点名。

    返回值必须与 ``add_conditional_edges`` 里声明的映射键一致，
    否则 LangGraph 会在运行时报“未定义的下一节点”。
    """
    intent = state.get("intent")
    if intent == "query_database":
        return "query_database"
    if intent == "anomaly_analysis":
        return "anomaly_analysis"
    if intent == "direct_answer":
        return "direct_answer"
    raise ValueError(f"无法分流未知意图: {intent!r}")


# 控制流：改写 -> 意图识别 -> 按意图分流到对应子 Agent -> 结束。
# Orchestrator 负责“理解 + 路由”，不落地任何 SQL 或归因逻辑。
builder = StateGraph(OrchestratorState, context_schema=DataAgentContext)
builder.add_node("rewrite_question", rewrite_question)
builder.add_node("intent_detection", intent_detection)
builder.add_node("query_database", run_data_agent)
builder.add_node("anomaly_analysis", run_anomaly_agent)
builder.add_node("direct_answer", run_direct_answer)
builder.add_edge(START, "rewrite_question")
builder.add_edge("rewrite_question", "intent_detection")
# 三个分支的目标节点名与 route_intent 的返回值一一对应。
builder.add_conditional_edges(
    "intent_detection",
    route_intent,
    {
        "query_database": "query_database",
        "anomaly_analysis": "anomaly_analysis",
        "direct_answer": "direct_answer",
    },
)
builder.add_edge("query_database", END)
builder.add_edge("anomaly_analysis", END)
builder.add_edge("direct_answer", END)

orchestrator_graph = builder.compile()
