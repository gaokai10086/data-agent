"""Main_RootCause_Agent 第一阶段与变化检验子图的结构回归。"""
from langgraph.constants import END

from app.agent.anomaly_agent.Change_Inspection_Agent.graph import anomaly_preparation_agent
from app.agent.anomaly_agent.Main_RootCause_Agent.graph import main_root_cause_agent


def test_main_root_cause_graph_runs_only_phase1_chain():
    assert set(main_root_cause_agent.nodes) >= {
        "receive_question", "extract_metrics", "select_skill", "load_skill",
        "select_task_schema", "parse_task_request",
    }
    edges = {(e.source, e.target) for e in main_root_cause_agent.get_graph().edges}
    assert ("receive_question", "extract_metrics") in edges
    assert ("extract_metrics", "select_skill") in edges
    assert ("load_skill", "select_task_schema") in edges
    assert ("parse_task_request", END) in edges
    assert ("select_skill", END) in edges


def test_change_inspection_graph_is_preserved():
    assert set(anomaly_preparation_agent.nodes) >= {
        "load_metric_context", "query_overall", "verify_change",
        "mark_preparation_result",
    }
