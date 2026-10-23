"""总体对比契约与处理器的离线测试。"""

import asyncio
import logging
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import asynccontextmanager

from app.agent.anomaly_agent.handlers.overall_comparison import (
    OverallComparisonHandler,
    calculate_change,
    extract_single_value,
)
from app.agent.anomaly_agent.handlers.registry import (
    UnsupportedAnalysisTypeError,
    get_anomaly_handler,
)
from app.agent.anomaly_agent.schemas import AnalysisRequest, MetricContext
from app.agent.anomaly_agent.nodes.query_overall import query_overall
from app.core.context import query_task_ctx_var, query_task_context
from app.core.logging import logger


class OverallContractTest(unittest.TestCase):
    @staticmethod
    def session_factory():
        @asynccontextmanager
        async def session_context():
            yield object()
        return session_context()

    def test_values_and_change(self) -> None:
        current = extract_single_value([{"value": "120"}], "2025-07")
        comparison = extract_single_value([{"value": 100}], "2025-06")
        self.assertEqual((current, comparison), (Decimal("120"), Decimal("100")))
        self.assertEqual(
            calculate_change(current, comparison),
            (Decimal("20"), Decimal("0.2")),
        )

    def test_null_and_zero_are_distinct(self) -> None:
        self.assertEqual(calculate_change(None, Decimal("100")), (None, None))
        self.assertEqual(calculate_change(Decimal("10"), Decimal("0")), (Decimal("10"), None))

    def test_invalid_shape_and_number_fail(self) -> None:
        for rows in ([], [{"current_value": 1}], [{"current_value": 1, "comparison_value": 2}] * 2,
                     [{"current_value": True, "comparison_value": 2}],
                     [{"current_value": "NaN", "comparison_value": 2}]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                extract_single_value(rows, "2025-07")

    def test_registry_rejects_unsupported_analysis(self) -> None:
        self.assertIsInstance(get_anomaly_handler("overall_comparison"), OverallComparisonHandler)
        with self.assertRaises(UnsupportedAnalysisTypeError):
            get_anomaly_handler("trend")

    def test_handler_calls_data_agent_and_builds_contract(self) -> None:
        request = AnalysisRequest(
            metric="销售额", target_period="2025-07", comparison_period="2025-06",
            comparison_type="mom",
        )
        metric = MetricContext(
            metric_key="sales_amount", name="销售额", fact_table="orders",
            measure_column="amount", aggregation="SUM",
        )
        events = []

        async def fake_astream(**kwargs):
            query = kwargs["input"]["query"]
            self.assertIn("销售额", query)
            yield "custom", {"stage": "执行SQL语句"}
            value = 120 if "2025-07" in query else 100
            yield "values", {"sql_result": [{"value": value}]}

        runtime = SimpleNamespace(
            context={"dw_session_factory": self.session_factory},
            stream_writer=events.append,
        )
        with patch(
            "app.agent.anomaly_agent.handlers.overall_comparison.data_query_graph.astream",
            side_effect=fake_astream,
        ):
            result = asyncio.run(OverallComparisonHandler().run(
                {"analysis_request": request, "metric_context": metric}, runtime
            ))

        self.assertEqual(result.analysis_type, "overall_comparison")
        self.assertEqual(result.request_id, request.request_id)
        self.assertEqual(result.change_amount, Decimal("20"))
        self.assertEqual(result.change_rate, Decimal("0.2"))
        self.assertEqual(len(events), 2)

    def test_handler_runs_two_period_queries_concurrently(self) -> None:
        request = AnalysisRequest(
            metric="销售额", target_period="2025-07", comparison_period="2025-06",
            comparison_type="mom",
        )
        metric = MetricContext(
            metric_key="sales_amount", name="销售额", fact_table="orders",
            measure_column="amount", aggregation="SUM",
        )

        active = 0
        max_active = 0

        async def fake_astream(**kwargs):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.05)
            value = 120 if "2025-07" in kwargs["input"]["query"] else 100
            try:
                yield "values", {"sql_result": [{"value": value}]}
            finally:
                active -= 1

        runtime = SimpleNamespace(
            context={"dw_session_factory": self.session_factory},
            stream_writer=lambda _: None,
        )
        with patch(
            "app.agent.anomaly_agent.handlers.overall_comparison.data_query_graph.astream",
            side_effect=fake_astream,
        ):
            result = asyncio.run(OverallComparisonHandler().run(
                {"analysis_request": request, "metric_context": metric}, runtime
            ))
        self.assertEqual(result.current_value, Decimal("120"))
        self.assertEqual(result.comparison_value, Decimal("100"))
        self.assertEqual(max_active, 2)

    def test_parallel_query_logs_keep_their_task_identity(self) -> None:
        request = AnalysisRequest(
            request_id="trace-request", metric="订单数量", target_period="2025-07",
            comparison_period="2025-06", comparison_type="mom",
        )
        metric = MetricContext(metric_key="order_count", name="订单数量", fact_table="fact_order",
                               measure_column="order_sn", aggregation="COUNT_DISTINCT")
        records, driver_messages, events = [], [], []

        class DriverHandler(logging.Handler):
            def emit(self, record):
                driver_messages.append(record.getMessage())

        async def fake_astream(**kwargs):
            period = "2025-07" if "2025-07" in kwargs["input"]["query"] else "2025-06"
            logger.info("召回开始")

            async def child_node():
                await asyncio.sleep(0)
                self.assertEqual(query_task_ctx_var.get().period, period)
                logger.info("召回结束")
                logging.getLogger("asyncmy").warning("执行计划 %s", period)

            # LangGraph 内部会创建节点子任务，验证标识随子任务传播。
            await asyncio.create_task(child_node())
            yield "custom", {"stage": "召回"}
            yield "values", {"sql_result": [{"value": 80 if period == "2025-07" else 100}]}

        sink_id = logger.add(lambda message: records.append(message.record),
                             format="{extra[query_prefix]}{message}")
        driver_handler = DriverHandler()
        driver = logging.getLogger("asyncmy")
        driver.addHandler(driver_handler)
        try:
            with patch("app.agent.anomaly_agent.handlers.overall_comparison.data_query_graph.astream", side_effect=fake_astream):
                asyncio.run(OverallComparisonHandler().run(
                    {"analysis_request": request, "metric_context": metric},
                    SimpleNamespace(context={"dw_session_factory": self.session_factory}, stream_writer=events.append),
                ))
            logger.info("父任务完成")
        finally:
            logger.remove(sink_id)
            driver.removeHandler(driver_handler)

        scoped = [record for record in records if record["message"] in {"召回开始", "召回结束"}]
        self.assertEqual(len(scoped), 4)
        for record in scoped:
            extra = record["extra"]
            role = "目标" if extra["query_period"] == "2025-07" else "对比"
            self.assertEqual(extra["query_role"], role)
            self.assertEqual(extra["query_task_id"], f"trace-request:{role}:{extra['query_period']}")
            self.assertRegex(extra["query_prefix"], r"^\[data-agent-\d+\] $")
        prefixes = {record["extra"]["query_period"]: record["extra"]["query_prefix"] for record in scoped}
        self.assertEqual(len(set(prefixes.values())), 2)
        for period, prefix in prefixes.items():
            self.assertIn(f"{prefix}执行计划 {period}", driver_messages)
        self.assertEqual({event["period"] for event in events}, {"2025-07", "2025-06"})
        self.assertTrue(all(event["query_task_id"] for event in events))
        self.assertEqual(records[-1]["extra"]["query_prefix"], "")
        self.assertIsNone(query_task_ctx_var.get())

    def test_query_log_scope_is_restored_after_failure(self) -> None:
        with self.assertRaises(RuntimeError):
            with query_task_context(request_id="failure-test", role="目标", period="2025-07", metric_key="metric"):
                raise RuntimeError("模拟任务失败")
        self.assertIsNone(query_task_ctx_var.get())

    def test_graph_node_routes_to_overall_contract(self) -> None:
        request = AnalysisRequest(
            metric="销售额", target_period="2025-07", comparison_period="2025-06",
            comparison_type="mom",
        )
        metric = MetricContext(
            metric_key="sales_amount", name="销售额", fact_table="orders",
            measure_column="amount", aggregation="SUM",
        )

        async def fake_astream(**kwargs):
            value = 120 if "2025-07" in kwargs["input"]["query"] else 100
            yield "values", {"sql_result": [{"value": value}]}

        runtime = SimpleNamespace(
            context={"dw_session_factory": self.session_factory},
            stream_writer=lambda _: None,
        )
        with patch(
            "app.agent.anomaly_agent.handlers.overall_comparison.data_query_graph.astream",
            side_effect=fake_astream,
        ):
            update = asyncio.run(query_overall(
                {"analysis_request": request, "metric_context": metric}, runtime
            ))

        self.assertEqual(set(update), {"analysis_result"})
        self.assertEqual(update["analysis_result"].analysis_type, "overall_comparison")
        self.assertEqual(update["analysis_result"].current_value, Decimal("120"))
        self.assertEqual(update["analysis_result"].comparison_value, Decimal("100"))


if __name__ == "__main__":
    unittest.main()
