"""verify_metric_change 确定性工具测试；不依赖真实 LLM 或数据库。"""

from __future__ import annotations

import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from app.agent.anomaly_agent.common.change_inspection.tools.overall_comparison import (
    OverallComparisonHandler,
)
from app.agent.anomaly_agent.common import verify_metric_change
from app.agent.anomaly_agent.common.data_query import DataQueryRequest, DataQueryResult
from app.agent.anomaly_agent.schemas import (
    AnalysisRequest,
    ChangeVerification,
    FilterCondition,
    InspectionOutcome,
    MetricContext,
    OverallComparisonResult,
)


def make_request(
    direction: str = "decrease",
    request_id: str = "req-1",
    filters: list[FilterCondition] | None = None,
) -> AnalysisRequest:
    return AnalysisRequest(
        metric="销售额",
        request_id=request_id,
        target_period="2025-07",
        comparison_period="2025-06",
        comparison_type="mom",
        expected_direction=direction,
        filters=filters or [],
    )


def make_metric(metric_key: str = "sales_amount") -> MetricContext:
    return MetricContext(
        metric_key=metric_key,
        name="销售额",
        fact_table="fact_order",
        measure_column="pay_amount",
        aggregation="SUM",
        time_column="pay_time",
    )


def make_result(
    current: str,
    comparison: str,
    *,
    request_id: str = "req-1",
    metric_key: str = "sales_amount",
) -> OverallComparisonResult:
    current_value = Decimal(current)
    comparison_value = Decimal(comparison)
    change_amount = current_value - comparison_value
    change_rate = None if comparison_value == 0 else change_amount / comparison_value
    return OverallComparisonResult(
        request_id=request_id,
        metric_key=metric_key,
        target_period="2025-07",
        comparison_period="2025-06",
        current_value=current_value,
        comparison_value=comparison_value,
        change_amount=change_amount,
        change_rate=change_rate,
    )


class VerifyMetricChangeTest(unittest.IsolatedAsyncioTestCase):
    def make_runtime(self) -> SimpleNamespace:
        return SimpleNamespace(context={})

    async def run_with_result(self, request, result, metric=None):
        metric = metric or make_metric()
        with patch(
            "app.agent.anomaly_agent.common.change_inspection.tool.resolve_metric_context",
            new=AsyncMock(return_value=metric),
        ), patch.object(
            OverallComparisonHandler,
            "compare",
            new=AsyncMock(return_value=result),
        ):
            return await verify_metric_change(request, self.make_runtime())

    async def test_decrease_as_expected_is_ready(self):
        outcome = await self.run_with_result(
            make_request("decrease"),
            make_result("80", "100"),
        )
        self.assertEqual(outcome.status, "ready")
        self.assertEqual(outcome.reason, "change_confirmed")
        self.assertEqual(outcome.overall_result.change_amount, Decimal("-20"))

    async def test_increase_as_expected_is_ready(self):
        outcome = await self.run_with_result(
            make_request("increase"),
            make_result("120", "100"),
        )
        self.assertEqual(outcome.status, "ready")
        self.assertEqual(outcome.overall_result.change_amount, Decimal("20"))

    async def test_expected_decrease_but_actual_increase_is_rejected(self):
        outcome = await self.run_with_result(
            make_request("decrease"),
            make_result("120", "100"),
        )
        self.assertEqual(outcome.status, "rejected")
        self.assertEqual(outcome.reason, "direction_mismatch")

    async def test_equal_periods_is_rejected(self):
        outcome = await self.run_with_result(
            make_request("decrease"),
            make_result("100", "100"),
        )
        self.assertEqual(outcome.status, "rejected")
        self.assertEqual(outcome.reason, "no_change")

    async def test_unspecified_direction_with_change_is_ready(self):
        outcome = await self.run_with_result(
            make_request("unspecified"),
            make_result("120", "100"),
        )
        self.assertEqual(outcome.status, "ready")
        self.assertEqual(outcome.verification.actual_direction, "increase")

    async def test_metadata_missing_returns_failed(self):
        with patch(
            "app.agent.anomaly_agent.common.change_inspection.tool.resolve_metric_context",
            new=AsyncMock(side_effect=ValueError("Metadata 中不存在已发布指标：销售额")),
        ):
            outcome = await verify_metric_change(
                make_request(),
                self.make_runtime(),
            )
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.reason, "metric_context_load_failed")
        self.assertNotEqual(outcome.user_message, "")

    async def test_data_query_tool_failure_returns_failed(self):
        metric = make_metric()
        failed_result = DataQueryResult(
            success=False,
            error_code="query_failed",
            error_message="数据库连接失败",
            request_id="req-1",
            purpose="anomaly_overall_comparison",
        )
        fake_tool = SimpleNamespace(run=AsyncMock(return_value=failed_result))
        with patch(
            "app.agent.anomaly_agent.common.change_inspection.tool.resolve_metric_context",
            new=AsyncMock(return_value=metric),
        ), patch.object(
            OverallComparisonHandler,
            "data_query_tool",
            fake_tool,
        ):
            outcome = await verify_metric_change(
                make_request(),
                self.make_runtime(),
            )
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.reason, "overall_query_failed")

    async def test_missing_overall_result_is_not_ready(self):
        outcome = await self.run_with_result(make_request(), None)
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.reason, "overall_query_failed")

    async def test_request_id_mismatch_is_rejected(self):
        outcome = await self.run_with_result(
            make_request(request_id="req-1"),
            make_result("80", "100", request_id="req-other"),
        )
        self.assertEqual(outcome.status, "rejected")
        self.assertEqual(outcome.reason, "result_mismatch")

    async def test_ungrounded_filter_is_rejected_without_query(self):
        request = make_request(
            "decrease",
            filters=[FilterCondition(dimension=None, value="凯乐石")],
        )
        load_mock = AsyncMock()
        with patch(
            "app.agent.anomaly_agent.common.change_inspection.tool.resolve_metric_context",
            load_mock,
        ):
            outcome = await verify_metric_change(request, self.make_runtime())
        self.assertEqual(outcome.status, "rejected")
        self.assertEqual(outcome.reason, "ungrounded_filter")
        load_mock.assert_not_awaited()

    def test_ready_without_required_evidence_fails_validation(self):
        verification = ChangeVerification(
            decision="continue",
            reason="change_confirmed",
            expected_direction="decrease",
            actual_direction="decrease",
            summary="变化方向得到支持",
        )
        with self.assertRaises(ValidationError):
            InspectionOutcome(
                status="ready",
                reason="change_confirmed",
                request_id="req-1",
                user_message="ok",
                verification=verification,
            )

    def test_ready_with_mismatched_metric_key_fails_validation(self):
        metric = make_metric("sales_amount")
        result = make_result("80", "100", metric_key="other_metric")
        verification = ChangeVerification(
            decision="continue",
            reason="change_confirmed",
            expected_direction="decrease",
            actual_direction="decrease",
            summary="变化方向得到支持",
        )
        with self.assertRaises(ValidationError):
            InspectionOutcome(
                status="ready",
                reason="change_confirmed",
                request_id="req-1",
                user_message="ok",
                metric_context=metric,
                overall_result=result,
                verification=verification,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
