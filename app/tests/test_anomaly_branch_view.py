"""多路径报告导航和金额图的纯函数回归测试。"""

import unittest

from app.web.anomaly_branch_view import branch_children, decline_chart_options


class AnomalyBranchViewTest(unittest.TestCase):
    def test_children_require_verified_parent_and_complete_scope(self):
        root = {"task_id": "root", "depth": 0, "dimension_column": "channel",
                "parent_scope": [], "selected_members": ["旗舰", "商城"]}
        child = {"task_id": "a", "parent_task_id": "root", "depth": 1,
                 "dimension_column": "region",
                 "parent_scope": [{"dimension_column": "channel", "member_value": "旗舰"}],
                 "reconciliation": {"comparison_matches": True, "current_matches": True,
                                    "change_matches": True}}
        wrong_member = {**child, "task_id": "bad-member",
                        "parent_scope": [{"dimension_column": "channel", "member_value": "自营"}]}
        failed = {**child, "task_id": "failed",
                  "reconciliation": {"comparison_matches": False, "current_matches": True,
                                     "change_matches": True}}
        index = branch_children([root, child, wrong_member, failed])
        self.assertEqual(index, {("root", "旗舰"): child})

    def test_chart_keeps_signed_amount_and_selection(self):
        options = decline_chart_options({"selected_members": ["旗舰"], "rows": [
            {"成员": "商城", "变化金额": "-192573.13"},
            {"成员": "旗舰", "变化金额": "-245265.69"},
            {"成员": "自营", "变化金额": "10000"},
        ]})
        data = options["series"][0]["data"]
        self.assertEqual(options["yAxis"]["data"], ["旗舰", "商城", "自营"])
        self.assertEqual([item["kind"] for item in data], ["selected", "unselected", "offset"])
        self.assertEqual(data[0]["actual"], "-24.53")
        self.assertEqual(data[2]["actual"], "+1.00")
        self.assertIn("-24.53", data[0]["tooltip"]["formatter"])


if __name__ == "__main__":
    unittest.main()
