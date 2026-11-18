"""Anomaly SQL scope checks through the production validation node."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.agent.data_agent.nodes.validate_sql import validate_sql
from app.agent.anomaly_agent.DimensionAttribution_Agent.tests.test_skeleton import make_input
from app.agent.anomaly_agent.DimensionAttribution_Agent.tools import DimensionPeriodComparisonTool
from app.agent.anomaly_agent.schemas import FilterCondition, MetricDimension
from app.service.sql_validation_service import SQLValidationService


class AnomalySQLScopeTest(unittest.IsolatedAsyncioTestCase):
    async def test_wrong_city_with_unrelated_beijing_literal_is_rejected(self):
        sql = ("SELECT s.channel_type AS dimension_value, SUM(o.pay_amount) AS value "
               "FROM fact_order o JOIN dim_store s ON o.store_id=s.store_id "
               "WHERE s.city='上海' AND '北京'='北京' "
               "AND o.pay_time>='2026-07-01' AND o.pay_time<'2026-08-01' GROUP BY s.channel_type")
        attribution_input = make_input()
        attribution_input.request.filters = [FilterCondition(dimension="城市", value="北京")]
        DimensionPeriodComparisonTool._validate_sql_contract(
            sql, attribution_input.request, attribution_input.metric_context,
            MetricDimension(name="渠道", column="dim_store.channel_type"), "2026-07", [],
        )
        city = {"table_name": "dim_store", "column_name": "city", "value": "北京", "matched_text": "北京"}
        self.assertTrue(SQLValidationService.validate_grounded_city_values(sql, [city]))
        repo = SimpleNamespace(
            validate_sql=AsyncMock(),
            get_used_columns_from_sql=AsyncMock(return_value=["channel_type", "pay_amount", "store_id", "city"]),
            get_all_table_names=AsyncMock(return_value=["fact_order", "dim_store"]),
        )
        state = {"sql": sql, "query": "查询北京销售额，按渠道分组", "retrieved_values": [city],
                 "table_infos": [{"name": "fact_order", "columns": [{"name": "pay_amount"}, {"name": "store_id"}, {"name": "pay_time"}]},
                                 {"name": "dim_store", "columns": [{"name": "channel_type"}, {"name": "store_id"}, {"name": "city"}]}],
                 "table_relations": []}
        result = await validate_sql(state, SimpleNamespace(context={"dw_mysql_repository": repo}, stream_writer=lambda _: None))
        self.assertIn("城市取值校验失败", str(result.get("error") or result.get("error_response")))
        repo.validate_sql.assert_awaited_once()
