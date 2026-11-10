"""筛选条件 grounding 回归测试。"""
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase

from app.agent.anomaly_agent.Main_RootCause_Agent.parsing.filter_grounding import ground_task_filters
from app.agent.anomaly_agent.schemas import FilterCondition, MonthPeriod, TaskRequest


class FakeMetricRepo:
    def __init__(self, dimensions):
        self.metric = SimpleNamespace(name="销售额", dimensions=dimensions)

    async def get_metric_by_name(self, name):
        return self.metric if name == "销售额" else None


class FakeValueRepo:
    def __init__(self, values):
        self.values = values

    async def query(self, query, limit=20):
        return self.values


def make_task(value):
    return TaskRequest(
        objective="分析销售额变化",
        metrics=["销售额"],
        target_period=MonthPeriod(year=2026, month=8),
        comparison_period=MonthPeriod(year=2026, month=7),
        filters=[FilterCondition(value=value)],
        expected_direction="increase",
    )


class FilterGroundingTest(IsolatedAsyncioTestCase):
    def runtime(self, dimensions=None, values=None):
        dimensions = dimensions or [{"name": "城市", "column": "dim_store.city"}]
        values = values or [{"id": "1", "table_name": "dim_store", "column_name": "city", "value": "北京", "aliases": ["北京市"]}]
        return SimpleNamespace(context={
            "meta_mysql_repository": FakeMetricRepo(dimensions),
            "value_es_repository": FakeValueRepo(values),
        })

    async def test_bare_city(self):
        result = await ground_task_filters(make_task("北京"), "为什么北京销售额下降？", self.runtime())
        self.assertEqual(result.filters[0].dimension, "城市")
        self.assertEqual(result.filters[0].value, "北京")

    async def test_region_suffix(self):
        result = await ground_task_filters(make_task("北京地区"), "为什么北京地区销售额下降？", self.runtime())
        self.assertEqual((result.filters[0].dimension, result.filters[0].value), ("城市", "北京"))

    async def test_city_alias(self):
        result = await ground_task_filters(make_task("北京市"), "为什么北京市销售额下降？", self.runtime())
        self.assertEqual((result.filters[0].dimension, result.filters[0].value), ("城市", "北京"))

    async def test_ambiguous_city_columns_remain_ungrounded(self):
        result = await ground_task_filters(
            make_task("北京"),
            "为什么北京销售额下降？",
            self.runtime([
                {"name": "门店城市", "column": "dim_store.city"},
                {"name": "用户城市", "column": "dim_user.city"},
            ], [
                {"id": "1", "table_name": "dim_store", "column_name": "city", "value": "北京"},
                {"id": "2", "table_name": "dim_user", "column_name": "city", "value": "北京"},
            ]),
        )
        self.assertIsNone(result.filters[0].dimension)
