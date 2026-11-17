"""异动 Web 报告适配层的纯函数测试，不访问数据库或模型。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.service.anomaly_report import (  # noqa: E402
    build_anomaly_report,
    events_for_new_observations,
    final_anomaly_event,
)


def _state(*observations):
    return {"observations": list(observations), "stop_reason": "completed"}


class AnomalyReportAdapterTest(unittest.TestCase):
    def test_dimension_table_hides_duplicate_rates_and_formats_two_decimals(self):
        paths = [
            {"task_id": "root", "depth": 0, "dimension_column": "channel", "selected_member": "旗舰店",
             "reconciliation": {"comparison_matches": True, "current_matches": True, "change_matches": True},
             "attributions": [{"group_value": "旗舰店", "comparison_value": "100.1234",
                               "current_value": "60.1234", "change_amount": "-40.0000",
                               "net_contribution_rate": "0.4", "overall_contribution_rate": "0.4",
                               "parent_contribution_rate": "0.4"}]},
            {"task_id": "child", "depth": 1, "dimension_column": "region", "selected_member": "华东",
             "reconciliation": {"comparison_matches": True, "current_matches": True, "change_matches": True},
             "attributions": [{"group_value": "华东", "comparison_value": "30.1234",
                               "current_value": "10.1234", "change_amount": "-20.0000",
                               "net_contribution_rate": "0.2", "overall_contribution_rate": "0.2",
                               "parent_contribution_rate": "0.5"}]},
        ]
        report = build_anomaly_report(_state({"step": 1, "action_name": "dimension_attribution",
                                               "status": "completed", "attribution_result": {"paths": paths}}))
        root, child = report["drilldown_sections"]
        self.assertEqual(root["display_rows"][0]["对比值"], "100.12")
        self.assertEqual(root["display_rows"][0]["整体贡献率"], "40.00%")
        self.assertNotIn("净变化贡献率", root["rows"][0])
        self.assertNotIn("父级贡献率", root["display_rows"][0])
        self.assertEqual(child["display_rows"][0]["整体贡献率"], "20.00%")
        self.assertEqual(child["display_rows"][0]["父级贡献率"], "50.00%")
        self.assertEqual(child["rows"][0]["变化金额"], "-20.0000")

    def test_dimension_label_comes_from_inspection_metric_context(self):
        state = _state(
            {
                "step": 1, "action_name": "change_inspection", "status": "ready",
                "inspection_outcome": {"metric_context": {"dimensions": [
                    {"column": "dim_store.channel_type", "name": "销售渠道"},
                ]}},
                "evidence": {"metric": "销售额", "comparison_value": 100, "current_value": 50,
                             "change_amount": -50, "change_rate": -0.5},
            },
            {
                "step": 2, "action_name": "dimension_attribution", "status": "completed",
                "attribution_result": {"paths": [{
                    "task_id": "task-1", "dimension_column": "dim_store.channel_type",
                    "selected_member": "线下旗舰店", "depth": 0,
                    "reconciliation": {"comparison_matches": True, "current_matches": True, "change_matches": True},
                    "attributions": [{"group_value": "线下旗舰店", "comparison_value": 100,
                                      "current_value": 50, "change_amount": -50}],
                }]},
            },
        )
        report = build_anomaly_report(state)
        self.assertEqual(report["drilldown_sections"][0]["dimension_name"], "销售渠道")
        self.assertEqual(report["charts"][0]["title"], "销售渠道成员销售额变化")
        self.assertIn("销售渠道“线下旗舰店”", report["overview_insight"])
        self.assertNotIn("channel_type=", report["overview_insight"])
        self.assertIn("\n\n", report["overview_insight"])
        self.assertNotIn("父级贡献", report["overview_insight"])

    def test_emits_kpi_chart_diagnosis_and_final_snapshot(self):
        state = _state(
            {
                "step": 1,
                "action_name": "change_inspection",
                "status": "ready",
                "inspection_outcome": {"request_id": "req-1"},
                "evidence": {
                    "metric": "销售额", "target_period": "2026-08", "comparison_period": "2026-07",
                    "current_value": 120.0, "comparison_value": 200.0,
                    "change_amount": -80.0, "change_rate": -0.4,
                },
            },
            {
                "step": 2,
                "action_name": "dimension_attribution",
                "status": "completed",
                "completion_status": "complete",
                "attribution_result": {
                    "request_id": "req-1",
                    "paths": [{
                        "dimension_column": "channel",
                        "reconciliation": {
                            "comparison_matches": True, "current_matches": True, "change_matches": True,
                        },
                        "attributions": [{
                            "group_value": "线上", "comparison_value": 100,
                            "current_value": 40, "change_amount": -60,
                            "net_contribution_rate": 0.75,
                        }],
                        "sql_by_period": {"2026-07": "SELECT 1", "2026-08": "SELECT 2"},
                    }],
                },
            },
            {
                "step": 3,
                "action_name": "metric_decomposition",
                "status": "completed",
                "completion_status": "complete",
                "evidence": {"total_query_count": 2},
                "decomposition_result": {
                    "request_id": "req-1",
                    "reconciliation": {"matches": True},
                    "components": [
                        {"factor_name": "订单量", "contribution_amount": -50, "change_amount": -2},
                        {"factor_name": "客单价", "contribution_amount": -30, "change_amount": -1},
                    ],
                    "sql_by_period": {"2026-07": "SELECT 3", "2026-08": "SELECT 4"},
                },
            },
            {
                "step": 4,
                "action_name": "business_diagnosis",
                "status": "partial",
                "completion_status": "partial",
                "business_diagnosis_result": {
                    "status": "partial", "completion_status": "partial", "stop_reason": "evidence_gap",
                    "findings": [{"finding_id": "F1", "statement": "统计事实", "evidence_ids": ["ev-1"]}],
                    "hypotheses": [{"hypothesis_id": "H1", "status": "unverified", "statement": "待验证"}],
                    "evidence_ids": ["ev-1"],
                },
            },
        )
        emitted = set()
        events = events_for_new_observations(state, emitted)
        types = [event["type"] for event in events]
        self.assertEqual(len({event["event_id"] for event in events}), len(events))
        self.assertIn("anomaly_kpi", types)
        self.assertIn("anomaly_chart", types)
        self.assertIn("anomaly_sql_result", types)
        self.assertIn("anomaly_diagnosis", types)
        final = final_anomaly_event(state)
        self.assertEqual(final["type"], "anomaly_result")
        self.assertEqual(final["report"]["status"], "partial")
        self.assertEqual(len(final["report"]["charts"]), 2)
        self.assertEqual(final["report"]["request_id"], "req-1")
        self.assertEqual(len(final["report"]["sql_evidence"]), 4)

    def test_completed_charts_require_reconciliation_and_steps_are_deduplicated(self):
        state = _state({
            "step": 1,
            "action_name": "dimension_attribution",
            "status": "completed",
            "attribution_result": {"paths": [{"attributions": [{"group_value": "x"}]}]},
        })
        emitted = set()
        first = events_for_new_observations(state, emitted)
        second = events_for_new_observations(state, emitted)
        self.assertEqual(len(second), 0)
        self.assertNotIn("anomaly_chart", [event["type"] for event in first])

    def test_zero_values_still_produce_kpi(self):
        state = _state({
            "step": 1,
            "action_name": "change_inspection",
            "status": "ready",
            "evidence": {"current_value": 0, "comparison_value": 0, "change_amount": 0},
        })
        report = build_anomaly_report(state)
        self.assertIsNotNone(report["overall"])

    def test_rejected_direction_stops_without_downstream_charts(self):
        state = _state({
            "step": 1,
            "action_name": "change_inspection",
            "status": "rejected",
            "reason": "direction_mismatch",
            "evidence": {
                "metric": "销售额", "target_period": "2026-08", "comparison_period": "2026-07",
                "current_value": 120953.99, "comparison_value": 233378.95,
                "change_amount": -112424.96, "change_rate": -0.4821,
            },
        })
        state["stop_reason"] = "rejected"
        report = build_anomaly_report(state)
        self.assertEqual(report["status"], "rejected")
        self.assertEqual(report["completion_status"], "complete")
        self.assertEqual(report["stop_reason"], "rejected")
        self.assertEqual(report["charts"], [])
        self.assertIn("实际方向为下降", report["summary"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
