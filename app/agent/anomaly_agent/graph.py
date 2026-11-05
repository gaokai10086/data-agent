"""异动分析顶层 Graph：连接主 Agent 与变化核查子 Agent。"""

from langgraph.constants import END, START
from langgraph.graph import StateGraph

from app.agent.anomaly_agent.Change_Inspection_Agent.graph import anomaly_preparation_agent
from app.agent.anomaly_agent.Main_RootCause_Agent.graph import main_root_cause_agent
from app.agent.anomaly_agent.state import AnomalyState
from app.agent.data_agent.context import DataAgentContext


def build_anomaly_graph(*, checkpointer=None):
    """主 Agent 负责理解与决策，变化核查子 Agent 负责取数与核实。"""
    builder = StateGraph(AnomalyState, context_schema=DataAgentContext)
    builder.add_node("main_root_cause", main_root_cause_agent)
    builder.add_node("change_inspection", anomaly_preparation_agent)
    builder.add_edge(START, "main_root_cause")
    builder.add_edge("main_root_cause", "change_inspection")
    builder.add_edge("change_inspection", END)
    return builder.compile(checkpointer=checkpointer)


anomaly_graph = build_anomaly_graph()
