import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
import asyncio
from dataclasses import replace

from app.agent.anomaly_agent.DimensionAttribution_Agent.nodes import select_decline_members
from app.agent.anomaly_agent.DimensionAttribution_Agent.graph import dimension_attribution_graph
from app.agent.anomaly_agent.DimensionAttribution_Agent.tests.test_skeleton import make_input
from app.agent.anomaly_agent.DimensionAttribution_Agent.schemas import DimensionMemberAttribution, ReconciliationResult
from app.agent.anomaly_agent.DimensionAttribution_Agent.tools import ATTRIBUTION_TOOL_REGISTRY
from app.agent.anomaly_agent.schemas import MetricDimension
from app.service.anomaly_report import build_anomaly_report
from app.service.anomaly_narrative_llm import context_from_report


def member(name, change):
    return SimpleNamespace(group_value=name, change_amount=Decimal(change))


class DeclineSelectionTest(unittest.TestCase):
    def test_four_channels_select_three_for_80_percent(self):
        rows = [member("旗舰", "-245265.69"), member("商城", "-192573.13"),
                member("加盟", "-165524.23"), member("自营", "-54969.95")]
        selected, total, actual, uncovered, reason = select_decline_members(rows, Decimal("0.80"), 3)
        self.assertEqual([item.group_value for item in selected], ["旗舰", "商城", "加盟"])
        self.assertEqual(total, Decimal("658333.00"))
        self.assertEqual(actual.quantize(Decimal("0.0001")), Decimal("0.9165"))
        self.assertEqual(uncovered, Decimal("54969.95"))
        self.assertEqual(reason, "coverage_reached")

    def test_growth_offsets_net_but_not_decline_coverage(self):
        selected, total, actual, _, _ = select_decline_members(
            [member("A", "-80"), member("B", "-20"), member("C", "90")], Decimal("0.8"), 3)
        self.assertEqual([item.group_value for item in selected], ["A"])
        self.assertEqual(total, Decimal("100"))
        self.assertEqual(actual, Decimal("0.8"))

    def test_one_none_zero_and_child_limit(self):
        self.assertEqual(select_decline_members([member("A", "-5")], Decimal("0.8"), 3)[2], Decimal("1"))
        self.assertEqual(select_decline_members([member("A", "0"), member("B", "2")], Decimal("0.8"), 3)[4], "no_decline_members")
        self.assertEqual(select_decline_members([member("A", "-4"), member("B", "-3"), member("C", "-3")], Decimal("0.8"), 1)[4], "child_limit_reached")

    def test_equal_amounts_have_stable_member_order(self):
        selected, *_ = select_decline_members([member("B", "-5"), member("A", "-5")], Decimal("0.8"), 3)
        self.assertEqual([item.group_value for item in selected], ["A", "B"])

    def test_null_member_is_uncovered_not_selected(self):
        selected, total, actual, uncovered, reason = select_decline_members(
            [member(None, "-90"), member("A", "-10")], Decimal("0.8"), 3)
        self.assertEqual([item.group_value for item in selected], ["A"])
        self.assertEqual((total, actual, uncovered, reason),
                         (Decimal("100"), Decimal("0.1"), Decimal("90"), "unreachable_coverage"))


class MultiBranchGraphTest(unittest.TestCase):
    def setUp(self):
        self.input = make_input(tools=("dimension_period_comparison",))
        self.input.metric_context.dimensions = [
            MetricDimension(name=name, column=column) for name, column in (
                ("渠道", "dim_store.channel_type"), ("大区", "dim_store.region"),
                ("城市", "dim_store.city"), ("门店", "dim_store.store_name"),
            )
        ]
        self.input.inspection_outcome.overall_result.current_value = Decimal("0")
        self.input.inspection_outcome.overall_result.comparison_value = Decimal("100")
        self.input.inspection_outcome.overall_result.change_amount = Decimal("-100")

    async def fake_run(self, input_data, runtime, *, dimension_column, parent_scope, task_id, depth, overall, **_):
        if not parent_scope:
            amounts = [("A", "40"), ("B", "30"), ("C", "20"), ("D", "10")]
        else:
            amounts = [(f"{dimension_column.rsplit('.', 1)[-1]}-{parent_scope[-1].member_value}",
                        str(overall.comparison_value))]
        members = [DimensionMemberAttribution(
            group_value=name, comparison_value=Decimal(value), current_value=Decimal("0"),
            change_amount=-Decimal(value), overall_contribution_rate=Decimal(value) / Decimal("100"),
        ) for name, value in amounts]
        reconciliation = ReconciliationResult(
            comparison_sum=sum((m.comparison_value for m in members), Decimal("0")),
            current_sum=Decimal("0"), summed_change_amount=-overall.comparison_value,
            overall_comparison_value=overall.comparison_value, overall_current_value=overall.current_value,
            overall_change_amount=overall.change_amount, comparison_matches=True,
            current_matches=True, change_matches=True, tolerance=Decimal("0.01"),
        )
        return {"dimension": next(d for d in input_data.metric_context.dimensions if d.column == dimension_column),
                "task_id": task_id, "depth": depth, "parent_scope": parent_scope, "members": members,
                "reconciliation": reconciliation, "sql_by_period": {"2026-07": "SELECT 1", "2026-08": "SELECT 2"},
                "query_count": 2, "informative": True, "null_member_present": False}

    def test_bfs_reuses_dimension_across_scopes_and_preserves_parent(self):
        with patch.object(ATTRIBUTION_TOOL_REGISTRY["dimension_period_comparison"], "run", self.fake_run):
            state = asyncio.run(dimension_attribution_graph.ainvoke({"input": self.input}))
        paths = state["result"].paths
        self.assertEqual([p.depth for p in paths], [0, 1, 1, 1, 2, 2, 2, 3, 3, 3])
        self.assertEqual(paths[0].selected_members, ["A", "B", "C"])
        self.assertEqual(paths[0].actual_decline_coverage, Decimal("0.9"))
        self.assertEqual([p.parent_scope[0].member_value for p in paths[1:4]], ["A", "B", "C"])
        self.assertEqual(len({p.task_id for p in paths}), len(paths))
        self.assertTrue(all(p.parent_task_id for p in paths[1:]))
        self.assertEqual(state["result"].total_query_count, 20)
        self.assertEqual(state["result"].current_path, [])
        report = build_anomaly_report({"observations": [
            {"step": 1, "action_name": "change_inspection", "status": "ready",
             "evidence": {"metric": "销售额", "target_period": "2026-08", "comparison_period": "2026-07",
                          "comparison_value": "100", "current_value": "0", "change_amount": "-100", "change_rate": "-1"}},
            {"step": 2, "action_name": "dimension_attribution", "status": "completed",
             "attribution_result": state["result"].model_dump(mode="json")},
        ]})
        self.assertEqual(len(report["drilldown_sections"]), 10)
        self.assertIn("A", report["overview_insight"])
        self.assertIn("B", report["overview_insight"])
        self.assertIn("C", report["overview_insight"])
        self.assertIn("90.00%", report["overview_insight"])
        self.assertNotIn("最终定位到", report["overview_insight"])
        self.assertEqual(len({f.evidence_id for f in context_from_report(report).drilldowns}), 10)

    def test_failed_branch_does_not_poison_siblings(self):
        async def fail_b(*args, **kwargs):
            if kwargs.get("parent_scope") and kwargs["parent_scope"][0].member_value == "B":
                raise RuntimeError("synthetic B failure")
            return await self.fake_run(*args, **kwargs)
        with patch.object(ATTRIBUTION_TOOL_REGISTRY["dimension_period_comparison"], "run", fail_b):
            state = asyncio.run(dimension_attribution_graph.ainvoke({"input": self.input}))
        result = state["result"]
        self.assertTrue(any(p.parent_scope and p.parent_scope[0].member_value == "C" for p in result.paths))
        self.assertTrue(any(p.get("reason") == "execution_failed" for p in result.abandoned_paths))
        self.assertEqual(result.completion_status, "partial")

    def test_sql_budget_keeps_unqueried_branches(self):
        self.input.skill_strategy = replace(self.input.skill_strategy, max_query_budget=3)
        with patch.object(ATTRIBUTION_TOOL_REGISTRY["dimension_period_comparison"], "run", self.fake_run):
            state = asyncio.run(dimension_attribution_graph.ainvoke({"input": self.input}))
        result = state["result"]
        self.assertEqual(result.total_query_count, 2)
        self.assertEqual(len(result.paths), 1)
        self.assertEqual(len(result.pending_tasks), 3)
        self.assertEqual((result.completion_status, result.stop_reason), ("partial", "budget_exhausted"))

    def test_max_depth_stops_after_root(self):
        self.input.skill_strategy = replace(self.input.skill_strategy, max_depth=0)
        with patch.object(ATTRIBUTION_TOOL_REGISTRY["dimension_period_comparison"], "run", self.fake_run):
            state = asyncio.run(dimension_attribution_graph.ainvoke({"input": self.input}))
        result = state["result"]
        self.assertEqual(len(result.paths), 1)
        self.assertEqual(result.paths[0].branch_stop_reason, "max_depth_reached")
        self.assertEqual(result.pending_tasks, [])
