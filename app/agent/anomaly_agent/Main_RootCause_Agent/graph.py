"""Main_RootCause_Agent（主 Agent / 决策调度中心）的 Graph。

当前实现“问题理解 → Skill 选择”的最小链路；任务规划与调度、
证据管理与分析预算、结果汇总与报告将在后续节点中补入。
"""

from langgraph.constants import END, START
from langgraph.graph import StateGraph

from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.parse_task import parse_task
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.receive_question import (
    receive_question,
)
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.select_skill import select_skill
from app.agent.anomaly_agent.Main_RootCause_Agent.state import RootCauseState
from app.agent.data_agent.context import DataAgentContext


def build_root_cause_graph(*, checkpointer=None):
    """构建主 Agent：先理解问题，再按指标选择 Skill。"""

    builder = StateGraph(RootCauseState, context_schema=DataAgentContext)
    builder.add_node("receive_question", receive_question)
    builder.add_node("parse_task", parse_task)
    builder.add_node("select_skill", select_skill)
    builder.add_edge(START, "receive_question")
    builder.add_edge("receive_question", "parse_task")
    builder.add_edge("parse_task", "select_skill")
    builder.add_edge("select_skill", END)
    return builder.compile(checkpointer=checkpointer)


main_root_cause_agent = build_root_cause_graph()

