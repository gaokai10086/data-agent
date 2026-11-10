"""字段取值关联的离线回归，覆盖真实故障及普通问数入口。"""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from app.core.meta.value_grounding import city_value_aliases, match_value_expression, select_value_evidence
from app.repositories.es.value_es_repository import ValueESRepository
from app.service.sql_validation_service import SQLValidationService, TABLE_ALIAS_PATTERN
from app.service.value_index_rebuild_service import ValueIndexRebuildService
from app.agent.data_agent.nodes.column_recall import column_recall
from app.agent.anomaly_agent.schemas import (
    AnalysisContext, AnalysisRequest, ChangeVerification, MetricContext, OverallComparisonResult,
)
from app.agent.anomaly_agent.common.change_inspection.nodes.load_metric_context import _build_metric_context
from app.agent.anomaly_agent.Main_RootCause_Agent.skills.registry import build_analysis_strategy
from app.core.meta.dimension_dependencies import validate_dimension_dependencies
from app.service.dimension_dependency_service import validate_dependency_data


def city(table="dim_store"):
    return {"id": table + ":北京", "table_name": table, "column_name": "city",
            "value": "北京", "aliases": ["北京", "北京市"], "matched_text": "北京市"}


class GroundingRulesTest(unittest.TestCase):
    def test_alias_keeps_database_value(self):
        self.assertEqual(city_value_aliases("北京"), ["北京", "北京市"])
        self.assertEqual(match_value_expression("查询2025年北京市7月的销售额", city()), "北京市")

    def test_single_character_overlap_is_not_evidence(self):
        self.assertIsNone(match_value_expression("北京市", {"column_name": "region", "value": "华北"}))
        self.assertIsNone(match_value_expression("查询2025年北京市7月的销售额", {"column_name": "brand", "value": "美的"}))

    def test_store_name_does_not_become_city_filter(self):
        self.assertIsNone(match_value_expression("查询北京王府井旗舰店的销售额", city()))

    def test_metric_default_and_explicit_user_scope(self):
        metrics = [{"dimensions": [{"column": "dim_store.city"}]}]
        candidates = [city(), city("dim_user")]
        default = select_value_evidence("查询2025年北京市7月的销售额", candidates, metrics)
        explicit = select_value_evidence("查询用户所在北京市2025年7月的销售额", candidates, metrics)
        self.assertEqual([v["table_name"] for v in default], ["dim_store"])
        self.assertEqual([v["table_name"] for v in explicit], ["dim_user"])

    def test_city_sql_checks_field_scope_and_canonical_value(self):
        good = "SELECT SUM(o.pay_amount) FROM fact_order o JOIN dim_store s ON o.store_id=s.store_id WHERE s.city='北京'"
        self.assertEqual(SQLValidationService.validate_grounded_city_values(good, [city()]), [])
        for bad in (good.replace("'北京'", "'北京市'"), good.replace("s.city='北京'", "s.store_name LIKE '%北京%'"),
                    good.replace("s.city='北京'", "s.region='北京'"), good.replace("dim_store", "dim_user"),
                    good.replace("s.city='北京'", "1=1 /* s.city='北京' */")):
            with self.subTest(sql=bad):
                self.assertTrue(SQLValidationService.validate_grounded_city_values(bad, [city()]))

    def test_city_filter_accepts_legal_table_reference_forms(self):
        queries = [
            # 来自实际故障日志：无别名、换行、JOIN 后紧接 ON。
            """SELECT COALESCE(SUM(fact_order.pay_amount), 0) AS value
FROM fact_order
JOIN dim_store
  ON fact_order.store_id = dim_store.store_id
WHERE fact_order.order_status = '已完成'
  AND fact_order.pay_time >= '2025-07-01 00:00:00'
  AND fact_order.pay_time < '2025-08-01 00:00:00'
  AND dim_store.city = '北京'""",
            "SELECT SUM(o.pay_amount) FROM fact_order o JOIN dim_store s ON o.store_id=s.store_id WHERE s.city='北京'",
            "SELECT SUM(o.pay_amount) FROM fact_order AS o JOIN dim_store AS s ON o.store_id=s.store_id WHERE s.city='北京'",
            "SELECT SUM(pay_amount) FROM `fact_order` JOIN `dim_store` ON fact_order.store_id=dim_store.store_id WHERE `dim_store`.`city`='北京'",
            "SELECT SUM(o.pay_amount) FROM `dw`.`fact_order` AS `o` LEFT JOIN `dw`.`dim_store` AS `s` ON o.store_id=s.store_id WHERE `s`.`city`='北京'",
            "select sum(pay_amount) from dw.fact_order inner join dw.dim_store on fact_order.store_id=dim_store.store_id where dim_store.city='北京'",
            "SELECT SUM(pay_amount) FROM fact_order JOIN dim_store USING (store_id) WHERE city='北京'",
        ]
        for sql in queries:
            with self.subTest(sql=sql):
                references = [match.groups() for match in TABLE_ALIAS_PATTERN.finditer(sql)]
                self.assertEqual([table for table, _ in references], ["fact_order", "dim_store"])
                self.assertEqual(SQLValidationService.validate_grounded_city_values(sql, [city()]), [])
                self.assertIn("dim_store", SQLValidationService._collect_physical_table_aliases(
                    sql, ["fact_order", "dim_store"],
                ))

    def test_keyword_prefix_is_allowed_in_alias(self):
        sql = ("SELECT SUM(ordering.pay_amount) FROM fact_order ordering "
               "JOIN dim_store join_store ON ordering.store_id=join_store.store_id "
               "WHERE join_store.city='北京'")
        self.assertEqual([match.groups() for match in TABLE_ALIAS_PATTERN.finditer(sql)],
                         [("fact_order", "ordering"), ("dim_store", "join_store")])
        self.assertEqual(SQLValidationService.validate_grounded_city_values(sql, [city()]), [])

    def test_no_alias_city_query_still_rejects_wrong_predicates(self):
        base = ("SELECT SUM(fact_order.pay_amount) FROM fact_order JOIN dim_store "
                "ON fact_order.store_id=dim_store.store_id WHERE ")
        for predicate in ("dim_store.region='北京'", "dim_store.store_name LIKE '%北京%'",
                          "dim_user.city='北京'", "dim_store.city='北京市'",
                          "1=1 /* dim_store.city='北京' */"):
            with self.subTest(predicate=predicate):
                self.assertTrue(SQLValidationService.validate_grounded_city_values(base + predicate, [city()]))


class GroundingPipelineTest(unittest.IsolatedAsyncioTestCase):
    async def test_es_filters_spurious_hits_and_resolves_city_alias(self):
        client = SimpleNamespace(search=AsyncMock(return_value={"hits": {"hits": [
            {"_source": {"column_name": "region", "value": "华北"}}, {"_source": city()},
        ]}}))
        result = await ValueESRepository(client).query("北京市")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["value"], "北京")
        self.assertEqual(result[0]["matched_text"], "北京市")

    async def test_column_recall_preserves_examples(self):
        repository = SimpleNamespace(search=AsyncMock(return_value=[{
            "column_id": "c", "table_id": 3, "table_name": "dim_store", "column_name": "city",
            "examples": ["北京", "杭州"], "comment": "门店城市", "score": 0.8,
        }]))
        runtime = SimpleNamespace(context={"column_qdrant_repository": repository,
                                          "embedding_client": SimpleNamespace(embed_query=Mock(return_value=[1.0]))},
                                  stream_writer=lambda _: None)
        result = await column_recall({"query": "北京市", "keywords": []}, runtime)
        self.assertEqual(result["retrieved_columns"][0]["examples"], ["北京", "杭州"])

    async def test_targeted_index_sync_preserves_other_fields(self):
        client = SimpleNamespace(
            indices=SimpleNamespace(exists=AsyncMock(return_value=True), put_mapping=AsyncMock(),
                                    delete=AsyncMock(), refresh=AsyncMock()),
            delete_by_query=AsyncMock(), bulk=AsyncMock(return_value={"errors": False}),
        )
        column = SimpleNamespace(id="city-id", name="city", type="VARCHAR", value_type="closed", examples=["北京"])
        report = await ValueIndexRebuildService(None, client).sync_fields([(SimpleNamespace(name="dim_store", id=3), column)])
        client.indices.delete.assert_not_awaited()
        self.assertEqual(client.delete_by_query.call_args.kwargs["query"], {"terms": {"column_id": ["city-id"]}})
        self.assertEqual(report.indexed_values, 1)
        doc = client.bulk.call_args.kwargs["operations"][1]
        self.assertEqual(doc["aliases"], ["北京", "北京市"])


class DimensionDependencyTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def context(filter_name="城市", dependencies=True):
        dimensions = [
            {"name": "支付时间", "column": "fact_order.pay_time"},
            {"name": "门店", "column": "dim_store.store_name"},
            {"name": "大区", "column": "dim_store.region"},
            {"name": "渠道", "column": "dim_store.channel_type"},
            {"name": "用户", "column": "dim_user.user_id"},
            {"name": "促销活动", "column": "dim_promotion.promo_name"},
            {"name": "城市", "column": "dim_store.city", "aliases": ["门店城市"],
             "determines": ["dim_store.region"] if dependencies else []},
        ]
        metric = _build_metric_context(SimpleNamespace(
            metric_key="sales_amount", name="销售额", fact_table="fact_order", measure_column="pay_amount",
            aggregation="SUM", time_column="pay_time", dimensions=dimensions,
        ))
        request = AnalysisRequest(metric="销售额", request_id="dependency-test", target_period="2025-07",
                                  comparison_period="2025-06", comparison_type="mom",
                                  filters=[{"dimension": filter_name, "value": "北京"}])
        result = OverallComparisonResult(request_id=request.request_id, metric_key=metric.metric_key,
                                         target_period="2025-07", comparison_period="2025-06",
                                         current_value=80, comparison_value=100, change_amount=-20, change_rate="-0.2")
        return AnalysisContext(request=request, metric=metric, comparison=result,
                               verification=ChangeVerification(decision="continue", reason="change_confirmed",
                               expected_direction="unspecified", actual_direction="decrease", summary="已核实"))

    async def test_city_excludes_region_and_preserves_four_other_dimensions(self):
        for name in ("城市", "门店城市", "dim_store.city"):
            with self.subTest(name=name):
                context = self.context(name)
                before = context.model_dump()
                result = await build_analysis_strategy(context)
                self.assertEqual([step.dimension_column for step in result.steps], [
                    "dim_store.store_name", "dim_store.channel_type", "dim_user.user_id", "dim_promotion.promo_name",
                ])
                reasons = {item.dimension_column: item for item in result.excluded_dimensions}
                self.assertEqual(reasons["dim_store.city"].reason, "direct_filter")
                self.assertEqual(reasons["dim_store.region"].determined_by, "dim_store.city")
                self.assertEqual(reasons["fact_order.pay_time"].reason, "period_scope")
                self.assertEqual(context.model_dump(), before)

    async def test_no_dependency_does_not_guess_and_reverse_does_not_propagate(self):
        for context in (self.context(dependencies=False), self.context("大区")):
            result = await build_analysis_strategy(context)
            columns = {step.dimension_column for step in result.steps}
            self.assertIn("dim_store.city" if context.request.filters[0].dimension == "大区" else "dim_store.region", columns)

    async def test_transitive_dependencies_propagate(self):
        context = self.context()
        # 使用已有字段验证通用传递算法；这只是测试关系，不登记到实际 Metadata。
        context.metric.dimensions[2].determines = ["dim_store.channel_type"]
        result = await build_analysis_strategy(context)
        self.assertNotIn("dim_store.channel_type", {step.dimension_column for step in result.steps})

    async def test_invalid_dependencies_are_rejected(self):
        cases = [
            [{"column": "a.x", "determines": ["a.missing"]}],
            [{"column": "a.x", "determines": ["a.x"]}],
            [{"column": "a.x", "determines": ["a.y"]}, {"column": "a.y", "determines": ["a.x"]}],
            [{"column": "a.x"}, {"column": "a.x"}],
        ]
        for dimensions in cases:
            with self.subTest(dimensions=dimensions), self.assertRaises(ValueError):
                validate_dimension_dependencies(dimensions)

    async def test_publication_requires_data_validation(self):
        dimensions = [{"column": "dim_store.city", "determines": ["dim_store.region"]},
                      {"column": "dim_store.region"}]
        with self.assertRaises(ValueError):
            await validate_dependency_data(dimensions, None)
        dw = SimpleNamespace(check_dimension_dependency=AsyncMock(return_value=False))
        with self.assertRaises(ValueError):
            await validate_dependency_data(dimensions, dw)
        dw.check_dimension_dependency.return_value = True
        await validate_dependency_data(dimensions, dw)
        dw.check_dimension_dependency.assert_awaited_with("dim_store.city", "dim_store.region")


if __name__ == "__main__":
    unittest.main()
