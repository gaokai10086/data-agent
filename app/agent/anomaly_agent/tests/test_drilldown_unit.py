"""下钻回归：范围继承、父分组对账、跨范围判重和模型反馈。"""
import unittest
from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from app.agent.anomaly_agent.schemas import AnalysisAction, AnalysisObservation
from app.agent.anomaly_agent.scope_planning import (
    available_actions, resolve_scope, scope_depth, validate_action,
)
from app.agent.anomaly_agent.tests.test_analyze_change_unit import (
    make_context, calculate_contributions, sample_rows, node, tool,
)
from app.agent.anomaly_agent.skills.registry import build_analysis_strategy


DIMENSIONS = ["dim_store.store_name", "dim_store.channel_type", "dim_promotion.promo_name"]


def action(column, parent=None, value=None):
    return AnalysisAction(action="dimension_contribution", dimension_column=column, reason="验证下钻线索",
                          scope_ref={"observation_id": parent, "group_value": value} if parent else None)


def parent_observation():
    return AnalysisObservation(
        observation_id="dimension_1", action=action(DIMENSIONS[0]), status="ok", summary="门店结果",
        groups=calculate_contributions(make_context(), *sample_rows()),
    )


def child_rows():
    # 在线下分组内，40 vs 70；不能再与上海总体 80 vs 100 对账。
    return ([{"group_value": "活动A", "value": 10}, {"group_value": "活动B", "value": 30}],
            [{"group_value": "活动A", "value": 50}, {"group_value": "活动B", "value": 20}])


class ScopeResolutionTest(unittest.TestCase):
    def setUp(self):
        self.root = make_context()
        self.history = [parent_observation()]

    def test_reference_preserves_original_scope_and_uses_parent_totals(self):
        before = self.root.model_dump()
        scoped = validate_action(self.root, action(DIMENSIONS[2], "dimension_1", "线下"), DIMENSIONS, self.history)
        self.assertEqual([(f.dimension, f.value) for f in scoped.request.filters],
                         [("城市", "上海"), (DIMENSIONS[0], "线下")])
        self.assertEqual(scoped.request.request_id, self.root.request.request_id)
        self.assertEqual(scoped.request.target_period, "2025-07")
        self.assertEqual(scoped.request.comparison_period, "2025-06")
        self.assertEqual(scoped.metric.model_dump(), self.root.metric.model_dump())
        self.assertEqual(scoped.comparison.current_value, 40)
        self.assertEqual(scoped.comparison.comparison_value, 70)
        self.assertEqual(scoped.comparison.change_amount, -30)
        self.assertEqual(self.root.model_dump(), before)

    def test_child_contributions_use_parent_change_not_root_change(self):
        scoped = resolve_scope(self.root, action(DIMENSIONS[2], "dimension_1", "线下"), self.history)
        groups = calculate_contributions(scoped, *child_rows())
        self.assertEqual(groups[0].change_amount, -40)
        self.assertEqual(groups[0].contribution_rate, Decimal(-40) / Decimal(-30))
        with self.assertRaises(ValueError):
            calculate_contributions(scoped, *sample_rows())

    def test_reject_unknown_failed_or_invented_group(self):
        for parent, value in [("missing", "线下"), ("dimension_1", "虚构门店")]:
            with self.subTest(parent=parent, value=value), self.assertRaises(ValueError):
                resolve_scope(self.root, action(DIMENSIONS[2], parent, value), self.history)
        self.history[0].status = "error"
        with self.assertRaises(ValueError):
            resolve_scope(self.root, action(DIMENSIONS[2], "dimension_1", "线下"), self.history)

    def test_reject_fixed_dimension_and_model_supplied_filters(self):
        with self.assertRaises(ValueError):
            validate_action(self.root, action(DIMENSIONS[0], "dimension_1", "线下"), DIMENSIONS, self.history)
        with self.assertRaises(ValidationError):
            AnalysisAction(action="dimension_contribution", dimension_column=DIMENSIONS[2],
                           reason="非法", filters=[{"dimension": "city", "value": "北京"}])
        with self.assertRaises(ValidationError):
            AnalysisAction(action="finish", scope_ref={"observation_id": "dimension_1", "group_value": "线下"},
                           reason="结束")

    def test_transitive_dependency_exclusion_after_drilldown(self):
        store = next(d for d in self.root.metric.dimensions if d.column == DIMENSIONS[0])
        store.determines = [DIMENSIONS[1]]
        options = available_actions(self.root, DIMENSIONS, self.history)
        self.assertFalse(any(a.scope_ref and a.dimension_column == DIMENSIONS[1] for a in options))

    def test_same_dimension_allowed_in_different_scope_but_not_repeated_scope(self):
        chosen = action(DIMENSIONS[2], "dimension_1", "线下")
        child = AnalysisObservation(observation_id="dimension_2", action=chosen, status="ok", summary="活动",
            groups=calculate_contributions(resolve_scope(self.root, chosen, self.history), *child_rows()))
        self.history.append(child)
        with self.assertRaises(ValueError):
            validate_action(self.root, chosen, DIMENSIONS, self.history)
        # 活动仍可以在原请求范围、另一家门店范围查询。
        validate_action(self.root, action(DIMENSIONS[2]), DIMENSIONS, self.history)
        validate_action(self.root, action(DIMENSIONS[2], "dimension_1", "线上"), DIMENSIONS, self.history)

    def test_failed_scoped_action_not_retried(self):
        chosen = action(DIMENSIONS[2], "dimension_1", "线下")
        self.history.append(AnalysisObservation(observation_id="dimension_2", action=chosen,
                                                status="error", summary="失败"))
        with self.assertRaises(ValueError):
            validate_action(self.root, chosen, DIMENSIONS, self.history)

    def test_nested_scope_accumulates_filters_and_enforces_depth(self):
        first = action(DIMENSIONS[2], "dimension_1", "线下")
        self.history.append(AnalysisObservation(observation_id="dimension_2", action=first, status="ok",
            summary="活动", groups=calculate_contributions(resolve_scope(self.root, first, self.history), *child_rows())))
        second = action(DIMENSIONS[1], "dimension_2", "活动A")
        scoped = validate_action(self.root, second, DIMENSIONS, self.history)
        self.assertEqual([(f.dimension, f.value) for f in scoped.request.filters],
                         [("城市", "上海"), (DIMENSIONS[0], "线下"), (DIMENSIONS[2], "活动A")])
        self.assertEqual(scoped.comparison.current_value, 10)
        self.assertEqual(scoped.comparison.comparison_value, 50)
        self.assertEqual(scope_depth(second, self.history), 2)
        self.history.append(AnalysisObservation(observation_id="dimension_3", action=second, status="ok",
            summary="渠道", groups=calculate_contributions(scoped,
                [{"group_value": "渠道A", "value": 10}], [{"group_value": "渠道A", "value": 50}])))
        with self.assertRaisesRegex(ValueError, "深度"):
            resolve_scope(self.root, action(DIMENSIONS[2], "dimension_3", "渠道A"), self.history)

    def test_null_or_unshown_groups_cannot_be_referenced(self):
        with self.assertRaises(ValidationError):
            action(DIMENSIONS[2], "dimension_1", None)
        self.history[0].groups = [self.history[0].groups[0].model_copy(update={"group_value": str(i)})
                                  for i in range(11)]
        with self.assertRaises(ValueError):
            resolve_scope(self.root, action(DIMENSIONS[2], "dimension_1", "10"), self.history)

    def test_empty_period_allowed_only_when_parent_baseline_zero(self):
        self.history[0].groups[0].current_value = Decimal(0)
        scoped = resolve_scope(self.root, action(DIMENSIONS[2], "dimension_1", "线下"), self.history)
        groups = calculate_contributions(scoped, [], [{"group_value": "活动A", "value": 70}])
        self.assertEqual(groups[0].current_value, 0)
        self.assertEqual(groups[0].contribution_rate, 1)
        with self.assertRaises(ValueError):
            calculate_contributions(self.root, [], sample_rows()[1])


class DrilldownLoopTest(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_reference_stops_before_second_query(self):
        root = make_context()
        state = {"analysis_request": root.request, "metric_context": root.metric,
                 "analysis_result": root.comparison, "change_verification": root.verification,
                 "analysis_strategy": await build_analysis_strategy(root)}
        executor = AsyncMock(return_value=(parent_observation().groups, {}))
        with patch.object(node, "decide_action", AsyncMock(side_effect=[
            action(DIMENSIONS[0]), action(DIMENSIONS[2], "dimension_1", "虚构门店")
        ])), patch.object(node, "query_dimension", executor):
            report = (await node.analyze_change(state, SimpleNamespace(context={}, stream_writer=lambda e: None)))["change_analysis_report"]
        self.assertEqual(report.stop_reason, "invalid_action")
        self.assertEqual(executor.await_count, 1)

    async def test_model_selects_store_then_activity_with_scoped_query_and_report(self):
        root, events = make_context(), []
        state = {"analysis_request": root.request, "metric_context": root.metric,
                 "analysis_result": root.comparison, "change_verification": root.verification,
                 "analysis_strategy": await build_analysis_strategy(root)}
        seen = []
        async def execute(context, column, runtime):
            seen.append((context, column))
            rows = sample_rows() if len(seen) == 1 else child_rows()
            return calculate_contributions(context, *rows), {"2025-07": "SELECT evidence"}
        choices = [action(DIMENSIONS[0]), action(DIMENSIONS[2], "dimension_1", "线下"),
                   AnalysisAction(action="finish", reason="完成阶段性定位")]
        with patch.object(node, "decide_action", AsyncMock(side_effect=choices)), patch.object(
            node, "query_dimension", side_effect=execute):
            report = (await node.analyze_change(state, SimpleNamespace(context={}, stream_writer=events.append)))["change_analysis_report"]
        self.assertEqual(report.stop_reason, "model_finished")
        self.assertEqual(len(seen[0][0].request.filters), 1)
        self.assertEqual(len(seen[1][0].request.filters), 2)
        self.assertEqual(report.observations[1].scope_depth, 1)
        self.assertEqual(report.observations[1].baseline_change_amount, -30)
        self.assertEqual(report.observations[1].contribution_basis, "query_scope_net_change")
        self.assertIn("线下", report.summary)
        self.assertIn("当前范围净变化：-30", report.summary)
        self.assertIn("不能与父级", report.summary)

    async def test_query_prompt_inherits_filters_and_reconciles_against_parent(self):
        root = make_context()
        scoped = resolve_scope(root, action(DIMENSIONS[2], "dimension_1", "线下"), [parent_observation()])
        queries = []
        @asynccontextmanager
        async def factory():
            yield object()
        async def stream(**kwargs):
            queries.append(kwargs["input"]["query"])
            yield "values", {"sql": "SELECT evidence", "sql_result": child_rows()[len(queries) - 1]}
        runtime = SimpleNamespace(context={"dw_session_factory": factory}, stream_writer=lambda e: None)
        with patch.object(tool.data_query_graph, "astream", side_effect=stream):
            groups, _ = await tool.query_dimension(scoped, DIMENSIONS[2], runtime)
        for query in queries:
            for text in ("城市=上海", "dim_store.store_name=线下", "已完成"):
                self.assertIn(text, query)
        self.assertEqual(groups[0].change_amount, -40)


if __name__ == "__main__":
    unittest.main()
