"""Main_RootCause_Agent（主 Agent / 决策调度中心）的 Graph。"""

from langgraph.constants import END, START
from langgraph.graph import StateGraph

from app.agent.anomaly_agent.Main_RootCause_Agent.actions import MAX_ACTION_EXECUTIONS
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.action_executor import action_executor
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.extract_metrics import extract_metrics
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.load_skill import load_skill_node
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.parse_task_request import parse_task_request
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.receive_question import (
    receive_question,
)
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.select_skill import select_skill
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.select_task_schema import select_task_schema
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.supervisor_reason import supervisor_reason
from app.agent.anomaly_agent.Main_RootCause_Agent.state import RootCauseState
from app.agent.data_agent.context import DataAgentContext


def route_skill(state: RootCauseState) -> str:
    """只有 matched 进入 load_skill，其余业务状态直接结束。"""
    skill_result = state.get("skill_result") or {}
    return "load_skill" if skill_result.get("status") == "matched" else END


def route_task_schema(state: RootCauseState) -> str:
    """只有 Schema 选择成功才进入结构化解析，否则受控结束。"""
    schema_result = state.get("schema_result") or {}
    return "parse_task_request" if schema_result.get("status") == "matched" else END


def route_to_supervisor(state: RootCauseState) -> str:
    parsed_result = state.get("parsed_result") or {}
    schema_result = state.get("schema_result") or {}
    if (
        parsed_result.get("status") == "passed"
        and schema_result.get("task_type") in {
            "period_comparison", "dimension_attribution", "metric_decomposition", "hypothesis_analysis",
        }
    ):
        return "supervisor_reason"
    return END


def route_after_reason(state: RootCauseState) -> str:
    decision = state.get("supervisor_decision") or {}
    if decision.get("action") == "call_tool" and decision.get("tool_name"):
        return "action_executor"
    return END


def route_after_action(state: RootCauseState) -> str:
    observations = state.get("observations", [])
    latest = observations[-1] if observations else {}
    if (
        latest.get("status") in {"rejected", "blocked"}
        or len(observations) >= MAX_ACTION_EXECUTIONS
    ):
        return END
    return "supervisor_reason"


def build_root_cause_graph(*, checkpointer=None):
    """构建主 Agent：接收 -> 指标提取 -> Skill 匹配 -> 条件加载。"""

    builder = StateGraph(RootCauseState, context_schema=DataAgentContext)
    builder.add_node("receive_question", receive_question)
    builder.add_node("extract_metrics", extract_metrics)
    builder.add_node("select_skill", select_skill)
    builder.add_node("load_skill", load_skill_node)
    builder.add_node("select_task_schema", select_task_schema)
    builder.add_node("parse_task_request", parse_task_request)
    builder.add_node("supervisor_reason", supervisor_reason)
    builder.add_node("action_executor", action_executor)
    builder.add_edge(START, "receive_question")
    builder.add_edge("receive_question", "extract_metrics")
    builder.add_edge("extract_metrics", "select_skill")
    builder.add_conditional_edges(
        "select_skill",
        route_skill,
        {"load_skill": "load_skill", END: END},
    )
    builder.add_edge("load_skill", "select_task_schema")
    builder.add_conditional_edges(
        "select_task_schema",
        route_task_schema,
        {"parse_task_request": "parse_task_request", END: END},
    )
    builder.add_conditional_edges(
        "parse_task_request",
        route_to_supervisor,
        {"supervisor_reason": "supervisor_reason", END: END},
    )
    builder.add_conditional_edges(
        "supervisor_reason",
        route_after_reason,
        {"action_executor": "action_executor", END: END},
    )
    builder.add_conditional_edges(
        "action_executor",
        route_after_action,
        {"supervisor_reason": "supervisor_reason", END: END},
    )
    return builder.compile(checkpointer=checkpointer)


main_root_cause_agent = build_root_cause_graph()

