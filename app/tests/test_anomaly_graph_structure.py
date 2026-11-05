"""异动前置 Agent、Main_RootCause_Agent 与受约束分析子图的结构回归。"""
from app.agent.anomaly_agent.graph import anomaly_graph
from app.agent.anomaly_agent.Change_Inspection_Agent.graph import anomaly_preparation_agent
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.analyze_change import analysis_graph


def test_anomaly_graph_registers_expected_nodes():
    assert set(anomaly_graph.nodes) >= {
        "anomaly_preparation_agent", "finish_preparation",
    }
    assert set(anomaly_preparation_agent.nodes) >= {
        "receive_question", "parse_request", "load_metric_context", "query_overall",
        "verify_change", "mark_preparation_result",
    }
    edges = {(e.source, e.target) for e in analysis_graph.get_graph().edges}
    assert ("check_evidence", "select_action") in edges
    assert ("execute_action", "check_evidence") in edges
    assert ("execute_action", "select_action") not in edges
