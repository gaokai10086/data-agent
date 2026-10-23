"""订单数量查询和销售额因素拆解的离线测试。"""
import unittest
from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.agent.anomaly_agent.handlers.metric_decomposition import (
    _count, calculate_metric_decomposition, execute_metric_decomposition,
)
from app.agent.anomaly_agent.tests.test_analyze_change_unit import make_context
from app.schemas.metric_decomposition import sales_amount_decomposition


def context_with_amounts(now="72000", before="100000"):
    context = make_context()
    context.metric.decomposition = sales_amount_decomposition()
    context.comparison.current_value = Decimal(now)
    context.comparison.comparison_value = Decimal(before)
    context.comparison.change_amount = Decimal(now) - Decimal(before)
    return context


class CalculationTest(unittest.TestCase):
    def test_reconciles_two_factors_and_keeps_filter_scope(self):
        result = calculate_metric_decomposition(context_with_amounts(), Decimal(800), Decimal(1000))
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.scope_filters[0].value, "上海")
        self.assertEqual(result.factors[0].effect_amount, Decimal(-19000))
        self.assertEqual(result.factors[1].effect_amount, Decimal(-9000))
        self.assertEqual(sum(f.effect_amount for f in result.factors), Decimal(-28000))
        self.assertEqual(result.factors[1].current_value, Decimal(90))

    def test_zero_count_is_unavailable(self):
        result = calculate_metric_decomposition(context_with_amounts("0", "100000"), Decimal(0), Decimal(1000))
        self.assertEqual((result.status, result.reason), ("unavailable", "zero_denominator"))
        self.assertEqual(result.factors, [])

    def test_canceling_effects_have_no_percentage(self):
        result = calculate_metric_decomposition(context_with_amounts("100000", "100000"), Decimal(800), Decimal(1000))
        self.assertEqual(sum(f.effect_amount for f in result.factors), 0)
        self.assertTrue(all(f.effect_rate is None for f in result.factors))

    def test_rejects_bad_count_and_amount_delta(self):
        for rows in ([], [{"value": None}], [{"value": True}], [{"value": "NaN"}],
                     [{"value": -1}], [{"value": "1.5"}], [{"value": 3, "extra": 1}]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                _count(rows, "2025-07")
        context = context_with_amounts()
        context.comparison.change_amount = Decimal(-1)
        with self.assertRaisesRegex(ValueError, "不一致"):
            calculate_metric_decomposition(context, Decimal(800), Decimal(1000))


class ExecutionTest(unittest.IsolatedAsyncioTestCase):
    async def test_queries_published_count_for_both_periods_with_inherited_scope(self):
        context = context_with_amounts()
        count_metric = SimpleNamespace(
            metric_key="order_count", name="订单数量", fact_table="fact_order",
            measure_column="order_sn", aggregation="COUNT_DISTINCT", time_column="pay_time",
            filters=[{"column": "fact_order.order_status", "operator": "=", "value": "已完成"}],
            status="published",
        )
        repository = SimpleNamespace(get_metric_by_key=AsyncMock(return_value=count_metric))

        @asynccontextmanager
        async def session_factory():
            yield object()

        queries = []

        async def fake_stream(*, input, context, stream_mode):
            queries.append(input["query"])
            index = len(queries) - 1
            yield "values", {"sql": f"SELECT COUNT(DISTINCT order_sn) AS value -- {index}",
                             "sql_result": [{"value": 800 if index == 0 else 1000}]}

        runtime = SimpleNamespace(context={"meta_mysql_repository": repository,
                                           "dw_session_factory": session_factory},
                                  stream_writer=lambda _: None)
        with patch("app.agent.anomaly_agent.handlers.metric_decomposition.data_query_graph.astream", fake_stream):
            result = await execute_metric_decomposition(context, runtime)
        self.assertEqual(result.status, "ready")
        self.assertEqual(len(queries), 2)
        self.assertIn("2025-07", queries[0])
        self.assertIn("2025-06", queries[1])
        self.assertTrue(all("上海" in q and "已完成" in q and "order_sn" in q for q in queries))
        self.assertEqual(set(result.sql_by_period), {"2025-07", "2025-06"})

    async def test_no_config_skips_queries(self):
        context = make_context()
        result = await execute_metric_decomposition(context, SimpleNamespace(context={}))
        self.assertEqual((result.status, result.reason), ("unavailable", "missing_decomposition"))
