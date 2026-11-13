import asyncio
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from app.agent.anomaly_agent.BusinessDiagnosis_Agent.tests.test_skeleton import make_business_input
from app.agent.anomaly_agent.BusinessDiagnosis_Agent.schemas import evidence_id
from app.agent.anomaly_agent.BusinessDiagnosis_Agent.tools import (
    ScopedMetricDecompositionError, ScopedMetricDecompositionTool,
)
from app.agent.anomaly_agent.schemas import FilterCondition, MetricDimension
from app.schemas.metric_decomposition import sales_amount_decomposition


class FakeQuery:
    def __init__(self):
        self.calls = []

    async def run(self, request, runtime):
        self.calls.append(request)
        value = {
            ("order_count", "2026-08"): "30", ("order_count", "2026-07"): "60",
            ("sales_amount", "2026-08"): "34723.06", ("sales_amount", "2026-07"): "121726.61",
        }[(request.metric_key, request.period)]
        end = "2026-09-01" if request.period == "2026-08" else "2026-08-01"
        return SimpleNamespace(
            success=True, rows=[{"value": value}], error_message=None,
            sql=(f"SELECT SUM(pay_amount) AS value FROM fact_order WHERE order_status='已完成' "
                 f"AND pay_time >= '{request.period}-01' AND pay_time < '{end}' "
                 "AND city='北京' AND channel_type='线下旗舰店'")
        )


def make_scoped_input():
    data = make_business_input()
    data.analysis_request.filters = [FilterCondition(dimension="城市", value="北京")]
    data.metric_context.dimensions[0].aliases = ["门店城市"]
    data.metric_context.dimensions.append(MetricDimension(name="渠道", column="dim_store.channel_type"))
    data.metric_context.decomposition = sales_amount_decomposition()
    data.inspection_outcome.metric_context = data.metric_context
    return data


class ScopedDecompositionToolTest(unittest.TestCase):
    def test_real_path_member_and_four_sql_reconcile(self):
        data = make_scoped_input()
        path = data.attribution_result.paths[0]
        parent_evidence_id = evidence_id("dimension_attribution", path.model_dump(mode="json"))
        fake = FakeQuery()
        runtime = SimpleNamespace(
            context={"meta_mysql_repository": SimpleNamespace(get_metric_by_key=self._metric)},
            stream_writer=lambda _event: None,
        )
        with patch("app.agent.anomaly_agent.BusinessDiagnosis_Agent.tools.DataQueryTool", return_value=fake):
            payload = asyncio.run(ScopedMetricDecompositionTool().run(
                data, runtime,
                tool_arguments={
                    "dimension_column": "dim_store.channel_type",
                    "member_value": "线下旗舰店",
                    "parent_evidence_id": parent_evidence_id,
                },
                query_budget_remaining=4,
            ))
        self.assertEqual(payload["query_count"], 4)
        self.assertEqual(len(fake.calls), 4)
        self.assertTrue(all("门店城市=北京" in call.query for call in fake.calls))
        self.assertTrue(all("dim_store.channel_type=线下旗舰店" in call.query for call in fake.calls))
        self.assertTrue(payload["reconciliation"].matches)
        self.assertEqual(payload["scope"][-1]["member_value"], "线下旗舰店")

    async def _metric(self, key):
        return SimpleNamespace(status="published", fact_table="fact_order", name="订单数量")

    def test_fabricated_member_rejected(self):
        data = make_scoped_input()
        path = data.attribution_result.paths[0]
        parent_evidence_id = evidence_id("dimension_attribution", path.model_dump(mode="json"))
        runtime = SimpleNamespace(context={"meta_mysql_repository": SimpleNamespace()}, stream_writer=lambda _event: None)
        with self.assertRaises(ScopedMetricDecompositionError):
            asyncio.run(ScopedMetricDecompositionTool().run(
                data, runtime,
                tool_arguments={
                    "dimension_column": "dim_store.channel_type",
                    "member_value": "虚构渠道",
                    "parent_evidence_id": parent_evidence_id,
                }, query_budget_remaining=4,
            ))

    def test_budget_requires_four_sql(self):
        with self.assertRaisesRegex(ScopedMetricDecompositionError, "4 条 SQL"):
            asyncio.run(ScopedMetricDecompositionTool().run(
                make_scoped_input(), SimpleNamespace(context={}, stream_writer=lambda _event: None),
                tool_arguments={}, query_budget_remaining=3,
            ))


if __name__ == "__main__":
    unittest.main()
