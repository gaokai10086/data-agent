"""Evidence-bound fallback wording for anomaly reports."""

import unittest

from app.service.anomaly_narrative_service import build_narratives


class NarrativeFallbackTest(unittest.TestCase):
    def test_compares_real_siblings_and_inherits_scope(self):
        overall = {"metric": "销售额", "target_period": "2026-08", "comparison_value": 1000000,
                   "current_value": 500000, "change_amount": -500000, "change_rate": -0.5}
        root = {"task_id": "root", "depth": 0, "dimension_column": "channel", "selected_member": "旗舰店",
                "reconciliation": {"comparison_matches": True, "current_matches": True, "change_matches": True},
                "attributions": [
                    {"group_value": "旗舰店", "change_amount": -300000, "overall_contribution_rate": 0.6},
                    {"group_value": "商城", "change_amount": -250000},
                    {"group_value": "自营", "change_amount": 50000},
                ]}
        child = {"task_id": "child", "depth": 1, "dimension_column": "region", "selected_member": "华东",
                 "parent_scope": [{"dimension_column": "channel", "member_value": "旗舰店"}],
                 "reconciliation": root["reconciliation"],
                 "attributions": [{"group_value": "华东", "change_amount": -300000}]}
        result = build_narratives(overall, None, [root, child], None,
                                  {"channel": "渠道", "region": "大区"})
        self.assertIn("商城", result["overview_insight"])
        self.assertIn("自营", result["overview_insight"])
        self.assertIn("增长约 5.00 万元", result["drilldown_insights"]["root"])
        self.assertIn("渠道“旗舰店”范围内", result["drilldown_insights"]["child"])

    def test_unreconciled_path_is_not_in_overview(self):
        path = {"task_id": "bad", "depth": 0, "dimension_column": "channel",
                "selected_member": "旗舰店", "attributions": [{"group_value": "旗舰店", "change_amount": -10}],
                "reconciliation": {"comparison_matches": False}}
        result = build_narratives(None, None, [path], None)
        self.assertNotIn("旗舰店", result["overview_insight"])


if __name__ == "__main__":
    unittest.main()
