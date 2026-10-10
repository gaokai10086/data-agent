"""异动分析 StateGraph 的结构回归测试。"""
from __future__ import annotations

from app.agent.anomaly_agent.graph import anomaly_graph


def test_anomaly_graph_registers_expected_nodes():
    assert set(anomaly_graph.nodes) >= {
        "parse_request",
        "load_context",
        "build_plan",
        "build_data_requests",
        "execute_queries",
        "analyze_results",
        "plan_drilldown",
        "generate_report",
    }

