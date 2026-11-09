"""Main_RootCause_Agent（主 Agent / 决策调度中心）的 Graph。"""

from langgraph.constants import END, START
from langgraph.graph import StateGraph

from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.extract_metrics import extract_metrics
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.load_skill import load_skill_node
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.receive_question import (
    receive_question,
)
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.select_skill import select_skill
from app.agent.anomaly_agent.Main_RootCause_Agent.state import RootCauseState
from app.agent.data_agent.context import DataAgentContext


def route_skill(state: RootCauseState) -> str:
    """只有 matched 进入 load_skill，其余业务状态直接结束。"""
    return "load_skill" if state.get("skill_match_status") == "matched" else END


def build_root_cause_graph(*, checkpointer=None):
    """构建主 Agent：接收 -> 指标提取 -> Skill 匹配 -> 条件加载。"""

    builder = StateGraph(RootCauseState, context_schema=DataAgentContext)
    builder.add_node("receive_question", receive_question)
    builder.add_node("extract_metrics", extract_metrics)
    builder.add_node("select_skill", select_skill)
    builder.add_node("load_skill", load_skill_node)
    builder.add_edge(START, "receive_question")
    builder.add_edge("receive_question", "extract_metrics")
    builder.add_edge("extract_metrics", "select_skill")
    builder.add_conditional_edges(
        "select_skill",
        route_skill,
        {"load_skill": "load_skill", END: END},
    )
    builder.add_edge("load_skill", END)
    return builder.compile(checkpointer=checkpointer)


main_root_cause_agent = build_root_cause_graph()

