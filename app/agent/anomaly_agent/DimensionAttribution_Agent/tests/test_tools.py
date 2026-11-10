"""维度归因确定性计算测试。"""
import unittest
from decimal import Decimal
from types import SimpleNamespace

from app.agent.anomaly_agent.DimensionAttribution_Agent.tests.test_skeleton import make_input
from app.agent.anomaly_agent.DimensionAttribution_Agent.tools import DimensionPeriodComparisonTool
from app.agent.anomaly_agent.schemas import FilterCondition, MetricDimension
from app.agent.anomaly_agent.DimensionAttribution_Agent.schemas import DimensionScope


class AttributionToolCalculationTest(unittest.TestCase):
    def overall(self, current="90", comparison="100", change="-10"):
        return SimpleNamespace(
            current_value=Decimal(current), comparison_value=Decimal(comparison),
            change_amount=Decimal(change),
        )

    def test_member_alignment_and_contribution(self):
        members, reconciliation = DimensionPeriodComparisonTool.compute_attributions(
            {"A": Decimal("40"), "B": Decimal("50")},
            {"A": Decimal("50"), "B": Decimal("50")},
            self.overall(),
        )
        self.assertEqual([item.group_value for item in members], ["A", "B"])
        self.assertEqual(members[0].change_amount, Decimal("-10"))
        self.assertEqual(members[0].net_contribution_rate, Decimal("1"))
        self.assertTrue(reconciliation.change_matches)

    def test_member_only_in_one_period_uses_zero(self):
        members, reconciliation = DimensionPeriodComparisonTool.compute_attributions(
            {"A": Decimal("10"), "B": Decimal("5")},
            {"A": Decimal("10")},
            self.overall(current="15", comparison="10", change="5"),
        )
        self.assertEqual(members[1].comparison_value, Decimal("0"))
        self.assertIsNone(members[1].change_rate)
        self.assertTrue(reconciliation.current_matches)

    def test_zero_overall_change_rate_is_none(self):
        members, reconciliation = DimensionPeriodComparisonTool.compute_attributions(
            {"A": Decimal("10")}, {"A": Decimal("10")},
            self.overall(current="10", comparison="10", change="0"),
        )
        self.assertIsNone(members[0].net_contribution_rate)
        self.assertTrue(reconciliation.change_matches)

    def test_reconciliation_failure_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "未对账"):
            DimensionPeriodComparisonTool.compute_attributions(
                {"A": Decimal("9")}, {"A": Decimal("10")}, self.overall(),
            )

    def test_null_member_is_preserved(self):
        rows = [{"dimension_value": None, "value": "3.00"}, {"dimension_value": "A", "value": "2.00"}]
        self.assertEqual(
            DimensionPeriodComparisonTool._normalize_rows(rows, "2026-07")[None], Decimal("3.00")
        )

    def test_dimension_resolution_requires_explicit_meta_match(self):
        input_data = make_input()
        input_data.request.filters = [FilterCondition(dimension="城市", value="北京")]
        with self.assertRaisesRegex(ValueError, "无法从 Meta 唯一确定"):
            DimensionPeriodComparisonTool.resolve_dimension(input_data)

    def test_task_id_changes_with_parent_scope_and_query_text_keeps_filters(self):
        input_data = make_input()
        input_data.request.filters = [FilterCondition(dimension="城市", value="北京")]
        scope = [DimensionScope(dimension_column="dim_store.channel_type", member_value="线上商城")]
        first = DimensionPeriodComparisonTool.build_task_id(input_data, "dim_store.city", [], 0)
        second = DimensionPeriodComparisonTool.build_task_id(input_data, "dim_store.city", scope, 1)
        self.assertNotEqual(first, second)
        text = DimensionPeriodComparisonTool()._query_text(
            input_data.request, input_data.metric_context,
            MetricDimension(name="城市", column="dim_store.city"),
            "2026-08", "目标", scope,
        )
        self.assertIn("城市=北京", text)
        self.assertIn("dim_store.channel_type =线上商城", text)

    def test_dimension_resolution_rejects_ambiguous_matches(self):
        input_data = make_input()
        input_data.raw_question = "分析渠道和城市销售额变化"
        input_data.metric_context.dimensions = [
            MetricDimension(name="渠道", column="dim_store.channel_type"),
            MetricDimension(name="城市", column="dim_store.city"),
        ]
        with self.assertRaisesRegex(ValueError, "无法从 Meta 唯一确定"):
            DimensionPeriodComparisonTool.resolve_dimension(input_data)


if __name__ == "__main__":
    unittest.main()
