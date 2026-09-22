"""指标提交服务：业务定义指标，系统校验后直接发布。"""
from app.models.mysql.column_metric_mysql import ColumnMetricMySQL
from app.models.mysql.metric_info_mysql import MetricInfoMySQL
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository
from app.schemas.metric_management import MetricSubmitRequest
from app.service.sql_validation_service import SQLValidationService

ALLOWED_AGGREGATIONS = frozenset({"SUM", "COUNT", "COUNT_DISTINCT", "AVG", "MIN", "MAX"})


class MetricSubmissionService:
    """只做技术校验，不做业务口径审批。业务人员对指标定义负责。"""

    def __init__(self, meta_repository: MetaMySQLRepository):
        self.meta_repository = meta_repository
        self.sql_validator = SQLValidationService(meta_repository=meta_repository)

    async def submit(self, request: MetricSubmitRequest) -> MetricInfoMySQL:
        metric_key = request.metric_key.strip()
        name = request.name.strip()
        fact_table = request.fact_table.strip()
        measure_column = request.measure_column.strip()
        aggregation = request.aggregation

        if await self.meta_repository.get_metric_by_key(metric_key):
            raise ValueError(f"指标标识 [{metric_key}] 已存在")
        if await self.meta_repository.get_metric_by_name(name):
            raise ValueError(f"指标名称 [{name}] 已存在")
        if aggregation not in ALLOWED_AGGREGATIONS:
            raise ValueError(f"聚合方式 [{aggregation}] 不合法")

        fact_table_record = await self.meta_repository.get_table_by_name(fact_table)
        if fact_table_record is None:
            raise ValueError(f"事实表 [{fact_table}] 不存在，请先完成 DW Schema 同步")

        measure_column_record = await self.sql_validator.resolve_column(measure_column, fact_table)

        if request.time_column:
            time_column = request.time_column.strip()
            await self.sql_validator.resolve_column(time_column, fact_table)

        # 校验过滤条件与维度引用的字段必须真实存在，避免业务拼错后影响 SQL 生成。
        for item in request.filters:
            await self.sql_validator.resolve_column(item.column.strip(), fact_table)
        for item in request.dimensions:
            await self.sql_validator.resolve_column(item.column.strip(), fact_table)

        metric = MetricInfoMySQL(
            metric_key=metric_key,
            name=name,
            description=request.description.strip(),
            formula=request.formula.strip(),
            fact_table=fact_table,
            measure_column=measure_column,
            aggregation=aggregation,
            time_column=request.time_column.strip() if request.time_column else None,
            filters=[item.model_dump() for item in request.filters],
            alias=[alias.strip() for alias in request.alias if alias.strip()],
            dimensions=[item.model_dump() for item in request.dimensions],
            relevant_columns=[f"{fact_table}.{measure_column}"],
            status="published",
            version=1,
        )
        await self.meta_repository.save_metric(metric)

        # 自动生成公式直接依赖的字段血缘。过滤条件与维度不是公式血缘，不写入 column_metric。
        existing_lineage = await self.meta_repository.get_column_metric(
            measure_column_record.id, metric.id
        )
        if existing_lineage is None:
            await self.meta_repository.save_column_metric(
                ColumnMetricMySQL(column_id=measure_column_record.id, metric_id=metric.id)
            )
        return metric
