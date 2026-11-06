"""verify_metric_change 工具契约测试；不调用真实模型或数据库。

运行：
python -m unittest app.agent.anomaly_agent.Change_Inspection_Agent.tools.test_verify_metric_change
"""

import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, Mock, patch

from app.agent.anomaly_agent.Change_Inspection_Agent.tools.verify_metric_change import (
    verify_metric_change,
)
from app.agent.anomaly_agent.schemas import (
    AnalysisRequest,
    InspectionOutcome,
    MetricContext,
    OverallComparisonResult,
)


class TestMetricChangeTool(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.request = AnalysisRequest(
            metric="销售额",
            target_period="2025-07",
            comparison_period="2025-06",
            comparison_type="mom",
            expected_direction="decrease",
        )
        self.metric = MetricContext(
            metric_key="sales_amount", name="销售额",
            fact_table="sales", measure_column="amount", aggregation="SUM",
        )
        self.runtime = Mock()

    def overall(self, current, comparison):
        return OverallComparisonResult(
            request_id=self.request.request_id,
            metric_key=self.metric.metric_key,
            target_period="2025-07",
            comparison_period="2025-06",
            current_value=Decimal(str(current)),
            comparison_value=Decimal(str(comparison)),
            change_amount=Decimal(str(current)) - Decimal(str(comparison)),
            change_rate=(
                (Decimal(str(current)) - Decimal(str(comparison))) / Decimal(str(comparison))
                if comparison else None
            ),
        )

    async def run_tool(self, overall=None, *, load_error=None, query_error=None):
        load = AsyncMock(
            side_effect=load_error,
            return_value={"metric_context": self.metric},
        )
        handler = Mock()
        handler.run = AsyncMock(side_effect=query_error, return_value=overall)
        with (
            patch(
                "app.agent.anomaly_agent.Change_Inspection_Agent.tools.verify_metric_change.load_metric_context",
                load,
            ),
            patch(
                "app.agent.anomaly_agent.Change_Inspection_Agent.tools.verify_metric_change.get_anomaly_handler",
                return_value=handler,
            ),
        ):
            result = await verify_metric_change(self.request, self.runtime)
        return result, load, handler

    async def test_ready_returns_comparison_evidence(self):
        result, load, handler = await self.run_tool(self.overall(80, 100))
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.reason, "change_confirmed")
        self.assertEqual(result.overall_result.change_amount, Decimal("-20"))
        self.assertEqual(result.verification.actual_direction, "decrease")
        self.assertEqual(result.metric_context.metric_key, "sales_amount")
        self.assertEqual(load.await_count, 1)
        self.assertEqual(handler.run.await_count, 1)

    async def test_direction_mismatch_is_rejected(self):
        result, _, _ = await self.run_tool(self.overall(120, 100))
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.reason, "direction_mismatch")
        self.assertEqual(result.verification.actual_direction, "increase")

    async def test_no_change_is_rejected(self):
        result, _, _ = await self.run_tool(self.overall(100, 100))
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.reason, "no_change")

    async def test_metadata_error_is_failed_and_query_not_called(self):
        result, _, handler = await self.run_tool(load_error=RuntimeError("metadata down"))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason, "inspection_execution_error")
        handler.run.assert_not_awaited()

    async def test_query_error_is_failed(self):
        result, _, _ = await self.run_tool(query_error=TimeoutError("timeout"))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.request_id, self.request.request_id)

    async def test_bad_input_rejected_before_execution(self):
        with self.assertRaises(TypeError):
            await verify_metric_change(None, self.runtime)

    def test_ready_requires_evidence(self):
        with self.assertRaises(ValueError):
            InspectionOutcome(
                status="ready", reason="change_confirmed",
                request_id=self.request.request_id,
                user_message="verified",
            )


if __name__ == "__main__":
    unittest.main()
