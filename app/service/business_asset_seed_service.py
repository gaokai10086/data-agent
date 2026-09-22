"""初始化第一版已确认的业务指标资产。"""
from dataclasses import dataclass
from typing import Any

from app.models.mysql.column_metric_mysql import ColumnMetricMySQL
from app.models.mysql.metric_example_mysql import MetricExampleMySQL
from app.models.mysql.metric_info_mysql import MetricInfoMySQL
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository


@dataclass
class BusinessAssetSeedReport:
    """初始化结果，用于命令行输出和后续管理后台展示。"""

    metrics_created: int = 0
    metrics_updated: int = 0
    lineages_created: int = 0
    lineages_existing: int = 0
    examples_created: int = 0
    examples_existing: int = 0


class BusinessAssetSeedService:
    """写入人工确认过的业务资产，不触碰 DW 数据和人工维护的 Schema 语义。"""

    # 当前资产由业务口径确认后写死在代码中；管理后台上线后，同一份结构由 API 接收。
    INITIAL_METRICS: tuple[dict[str, Any], ...] = (
        {
            "metric_key": "sales_amount",
            "name": "销售额",
            "description": "订单状态为“已完成”的订单实际支付金额之和。",
            "formula": "SUM(fact_order.pay_amount)",
            "fact_table": "fact_order",
            "measure_column": "pay_amount",
            "aggregation": "SUM",
            "time_column": "pay_time",
            "filters": [
                {"column": "fact_order.order_status", "operator": "=", "value": "已完成"}
            ],
            "alias": ["销售金额", "成交金额", "实付金额"],
            "dimensions": [
                {"column": "fact_order.pay_time", "name": "支付时间"},
                {"column": "dim_store.store_name", "name": "门店"},
                {"column": "dim_store.region", "name": "大区"},
                {"column": "dim_store.channel_type", "name": "渠道"},
                {"column": "dim_user.user_id", "name": "用户"},
                {"column": "dim_promotion.promo_name", "name": "促销活动"},
            ],
            # 只记录公式的直接依赖；过滤条件和维度不属于公式血缘。
            "lineage_columns": [("fact_order", "pay_amount")],
            "examples": [
                {
                    "example_type": "correct",
                    "question": "销售额的统计口径是什么？",
                    "content": "销售额是订单状态为“已完成”的订单实际支付金额之和，计算公式为 SUM(fact_order.pay_amount)。",
                    "reason": "解释了事实表、聚合字段、固定过滤条件，符合已发布口径。",
                },
                {
                    "example_type": "correct",
                    "question": "统计 2025 年 5 月的销售额",
                    "content": "SELECT SUM(pay_amount) AS sales_amount FROM fact_order WHERE order_status = '已完成' AND pay_time >= '2025-05-01' AND pay_time < '2025-06-01';",
                    "reason": "使用 pay_time 作为时间字段，并保留“已完成”固定过滤条件。",
                },
                {
                    "example_type": "wrong",
                    "question": "把所有订单的 original_amount 相加作为销售额",
                    "content": "SELECT SUM(original_amount) FROM fact_order;",
                    "reason": "original_amount 是优惠前原价，且遗漏了已完成订单过滤条件，不符合销售额口径。",
                },
                {
                    "example_type": "wrong",
                    "question": "用 fact_order_detail.total_sales_amount 统计销售额",
                    "content": "SELECT SUM(total_sales_amount) FROM fact_order_detail;",
                    "reason": "该字段是商品明细销售小计，可能用于商品分析；初版“销售额”定义为订单口径的 pay_amount。",
                },
            ],
        },
        {
            "metric_key": "order_count",
            "name": "订单数量",
            "description": "订单状态为“已完成”的去重订单数。",
            "formula": "COUNT(DISTINCT fact_order.order_sn)",
            "fact_table": "fact_order",
            "measure_column": "order_sn",
            "aggregation": "COUNT_DISTINCT",
            "time_column": "pay_time",
            "filters": [
                {"column": "fact_order.order_status", "operator": "=", "value": "已完成"}
            ],
            "alias": ["完成订单数", "成交订单数"],
            "dimensions": [
                {"column": "fact_order.pay_time", "name": "支付时间"},
                {"column": "dim_store.store_name", "name": "门店"},
                {"column": "dim_store.region", "name": "大区"},
                {"column": "dim_store.channel_type", "name": "渠道"},
                {"column": "dim_user.user_id", "name": "用户"},
                {"column": "dim_promotion.promo_name", "name": "促销活动"},
            ],
            "lineage_columns": [("fact_order", "order_sn")],
            "examples": [
                {
                    "example_type": "correct",
                    "question": "订单数量的统计口径是什么？",
                    "content": "订单数量是订单状态为“已完成”的去重订单数，计算公式为 COUNT(DISTINCT fact_order.order_sn)。",
                    "reason": "订单编号是订单粒度主键，去重可保证未来与明细表关联时不重复计数。",
                },
                {
                    "example_type": "correct",
                    "question": "统计华东地区 2025 年 5 月的订单数量",
                    "content": "SELECT COUNT(DISTINCT o.order_sn) AS order_count FROM fact_order AS o JOIN dim_store AS s ON o.store_id = s.store_id WHERE o.order_status = '已完成' AND o.pay_time >= '2025-05-01' AND o.pay_time < '2025-06-01' AND s.region = '华东';",
                    "reason": "使用已确认 Join 关系，并保留完成订单和支付时间的口径约束。",
                },
                {
                    "example_type": "wrong",
                    "question": "统计 fact_order 表的所有行数作为订单数量",
                    "content": "SELECT COUNT(*) FROM fact_order;",
                    "reason": "遗漏了“已完成”固定过滤条件；在关联明细表等场景中，COUNT(*) 还可能放大订单数量。",
                },
                {
                    "example_type": "wrong",
                    "question": "统计 fact_order_detail 的行数作为订单数量",
                    "content": "SELECT COUNT(*) FROM fact_order_detail;",
                    "reason": "明细表一笔订单可包含多件商品，行数代表订单商品明细数，不代表订单数。",
                },
            ],
        },
    )

    def __init__(self, meta_repository: MetaMySQLRepository):
        self.meta_repository = meta_repository

    async def seed(self) -> BusinessAssetSeedReport:
        report = BusinessAssetSeedReport()
        for metric_definition in self.INITIAL_METRICS:
            await self._validate_schema(metric_definition)
            metric, was_created, was_updated = await self._upsert_metric(metric_definition)
            if was_created:
                report.metrics_created += 1
            elif was_updated:
                report.metrics_updated += 1
            await self._seed_lineage(metric, metric_definition["lineage_columns"], report)
            await self._seed_examples(metric, metric_definition["examples"], report)
        return report

    async def _validate_schema(self, definition: dict[str, Any]) -> None:
        """指标发布前校验其依赖的物理资产，避免把错误口径写进 published。"""
        required_columns = [
            (definition["fact_table"], definition["measure_column"]),
            (definition["fact_table"], "order_status"),
            (definition["fact_table"], definition["time_column"]),
            *definition["lineage_columns"],
        ]
        for table_name, column_name in required_columns:
            table = await self.meta_repository.get_table_by_name(table_name)
            if table is None:
                raise RuntimeError(
                    f"发布指标 [{definition['name']}] 失败：Meta 中缺少表 [{table_name}]。请先执行 DW Schema 同步。"
                )
            column = await self.meta_repository.get_column(table.id, column_name)
            if column is None:
                raise RuntimeError(
                    f"发布指标 [{definition['name']}] 失败：Meta 中缺少字段 [{table_name}.{column_name}]。请先执行 DW Schema 同步。"
                )

    async def _upsert_metric(self, definition: dict[str, Any]) -> tuple[MetricInfoMySQL, bool, bool]:
        metric = await self.meta_repository.get_metric_by_key(definition["metric_key"])
        created = metric is None
        if metric is None:
            metric = MetricInfoMySQL(metric_key=definition["metric_key"], name=definition["name"])

        # 当前是受版本控制的种子资产：只有口径实际变化时才更新，避免重复运行产生无意义审计记录。
        updated = False
        for field_name in (
            "name", "description", "formula", "fact_table", "measure_column", "aggregation",
            "time_column", "filters", "alias", "dimensions",
        ):
            if getattr(metric, field_name) != definition[field_name]:
                setattr(metric, field_name, definition[field_name])
                updated = True
        relevant_columns = [f"{table}.{column}" for table, column in definition["lineage_columns"]]
        if metric.relevant_columns != relevant_columns:
            metric.relevant_columns = relevant_columns
            updated = True
        if metric.status != "published":
            metric.status = "published"
            updated = True
        if metric.version is None:
            metric.version = 1
            updated = True
        if created or updated:
            await self.meta_repository.save_metric(metric)
        return metric, created, updated

    async def _seed_lineage(
        self,
        metric: MetricInfoMySQL,
        lineage_columns: list[tuple[str, str]],
        report: BusinessAssetSeedReport,
    ) -> None:
        for table_name, column_name in lineage_columns:
            table = await self.meta_repository.get_table_by_name(table_name)
            if table is None:
                raise RuntimeError(f"Meta 中缺少表 [{table_name}]")
            column = await self.meta_repository.get_column(table.id, column_name)
            if column is None:
                raise RuntimeError(f"Meta 中缺少字段 [{table_name}.{column_name}]")
            existing = await self.meta_repository.get_column_metric(column.id, metric.id)
            if existing is not None:
                report.lineages_existing += 1
                continue
            await self.meta_repository.save_column_metric(
                ColumnMetricMySQL(column_id=column.id, metric_id=metric.id)
            )
            report.lineages_created += 1

    async def _seed_examples(
        self,
        metric: MetricInfoMySQL,
        examples: list[dict[str, str]],
        report: BusinessAssetSeedReport,
    ) -> None:
        for example_definition in examples:
            existing = await self.meta_repository.get_metric_example(
                metric.id,
                example_definition["example_type"],
                example_definition["question"],
            )
            if existing is not None:
                report.examples_existing += 1
                continue
            await self.meta_repository.save_metric_example(
                MetricExampleMySQL(metric_id=metric.id, **example_definition)
            )
            report.examples_created += 1
