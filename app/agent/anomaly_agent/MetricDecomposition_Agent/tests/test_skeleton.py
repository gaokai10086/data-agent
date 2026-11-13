import asyncio
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.agent.anomaly_agent.DimensionAttribution_Agent.tests.test_skeleton import make_input as make_dimension_input
from app.agent.anomaly_agent.MetricDecomposition_Agent.graph import metric_decomposition_graph
from app.agent.anomaly_agent.MetricDecomposition_Agent.schemas import MetricDecompositionInput
from app.agent.anomaly_agent.MetricDecomposition_Agent.tools import (
    DecompositionQueryError,
    MetricDecompositionTool,
    calculate_symmetric_product_decomposition,
)
from app.schemas.metric_decomposition import sales_amount_decomposition


def make_input():
    base = make_dimension_input()
    base.metric_context.decomposition = sales_amount_decomposition()
    base.inspection_outcome.metric_context = base.metric_context
    base.inspection_outcome.overall_result.current_value = Decimal("120")
    base.inspection_outcome.overall_result.comparison_value = Decimal("100")
    base.inspection_outcome.overall_result.change_amount = Decimal("20")
    return MetricDecompositionInput(
        raw_question="分析销售额变化构成",
        request=base.request,
        inspection_outcome=base.inspection_outcome,
        metric_context=base.metric_context,
        skill_name=base.skill_name,
        skill_version=base.skill_version,
        skill_strategy=base.skill_strategy,
    )


class MetricDecompositionSchemaTest(unittest.TestCase):
    def test_requires_ready_and_published_formula(self):
        input_data = make_input()
        self.assertEqual(input_data.metric_context.decomposition.type, "product")
        input_data.inspection_outcome.status = "rejected"
        with self.assertRaises(ValueError):
            MetricDecompositionInput.model_validate(input_data.model_dump())

    def test_unavailable_formula_is_rejected_at_input(self):
        base = make_dimension_input()
        input_data = MetricDecompositionInput(
            raw_question="x", request=base.request,
            inspection_outcome=base.inspection_outcome,
            metric_context=base.metric_context,
            skill_name=base.skill_name, skill_version=base.skill_version,
            skill_strategy=base.skill_strategy,
        )
        self.assertIsNone(input_data.metric_context.decomposition)


class MetricDecompositionCalculationTest(unittest.TestCase):
    def test_dependency_query_preserves_target_metric_city_alias(self):
        input_data = make_input()
        from app.agent.anomaly_agent.schemas import FilterCondition
        input_data.metric_context.dimensions[0].aliases = ["门店城市"]
        input_data.request.filters = [FilterCondition(dimension="城市", value="北京")]
        text = MetricDecompositionTool._query_text(
            "订单数量", "2026-08", input_data.request, input_data.metric_context,
        )
        self.assertIn("门店城市=北京", text)

    def test_symmetric_product_contributions_reconcile(self):
        components, reconciliation = calculate_symmetric_product_decomposition(
            current_metric=Decimal("120"), comparison_metric=Decimal("100"),
            current_count=Decimal("8"), comparison_count=Decimal("10"),
            overall_change=Decimal("20"),
        )
        self.assertEqual(sum(item.contribution_amount for item in components), Decimal("20"))
        self.assertTrue(reconciliation.matches)

    def test_zero_count_is_unavailable(self):
        with self.assertRaisesRegex(DecompositionQueryError, "零基数"):
            calculate_symmetric_product_decomposition(
                current_metric=Decimal("1"), comparison_metric=Decimal("0"),
                current_count=Decimal("0"), comparison_count=Decimal("2"),
                overall_change=Decimal("1"),
            )

    def test_zero_overall_change_has_no_contribution_rate(self):
        components, reconciliation = calculate_symmetric_product_decomposition(
            current_metric=Decimal("100"), comparison_metric=Decimal("100"),
            current_count=Decimal("10"), comparison_count=Decimal("10"),
            overall_change=Decimal("0"),
        )
        self.assertTrue(reconciliation.matches)
        self.assertTrue(all(item.contribution_rate is None for item in components))


class MetricDecompositionGraphTest(unittest.TestCase):
    def test_graph_without_formula_returns_unavailable(self):
        input_data = make_input()
        input_data.metric_context.decomposition = None
        result = asyncio.run(metric_decomposition_graph.ainvoke({"input": input_data}))
        self.assertEqual(result["result"].status, "unavailable")
        self.assertEqual(result["result"].reason, "formula_unavailable")

    def test_graph_formula_mismatch_returns_unavailable(self):
        input_data = make_input()
        input_data.metric_context.decomposition.factors[1].source.numerator_metric_key = "other_metric"
        result = asyncio.run(metric_decomposition_graph.ainvoke({"input": input_data}))
        self.assertEqual(result["result"].status, "unavailable")
        self.assertEqual(result["result"].reason, "formula_mismatch")

    def test_graph_with_formula_and_mocked_runtime_query(self):
        input_data = make_input()
        repo = SimpleNamespace(get_metric_by_key=AsyncMock(return_value=SimpleNamespace(
            status="published", fact_table="fact_order", name="订单数量",
        )))
        class FakeQuery:
            calls = []
            async def run(self, request, runtime):
                self.calls.append(request)
                value = "8" if request.metric_key == "order_count" and request.period == "2026-08" else (
                    "10" if request.metric_key == "order_count" else ("120" if request.period == "2026-08" else "100")
                )
                return SimpleNamespace(success=True, rows=[{"value": value}], sql=f"SELECT {value} AS value")
        fake = FakeQuery()
        runtime = SimpleNamespace(context={"meta_mysql_repository": repo})
        with patch("app.agent.anomaly_agent.MetricDecomposition_Agent.tools.DataQueryTool", return_value=fake):
            result = asyncio.run(metric_decomposition_graph.ainvoke({"input": input_data}, context=runtime.context))
        output = result["result"]
        self.assertEqual(output.status, "completed")
        self.assertEqual(output.completion_status, "complete")
        self.assertEqual(len(fake.calls), 4)
        self.assertTrue(output.reconciliation.matches)


if __name__ == "__main__":
    unittest.main()
