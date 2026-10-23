"""发布与读取时检查拆解引用及基础口径；不把元数据一致性当作数据质量证明。"""
import json

from app.schemas.metric_decomposition import MetricDecomposition


def _qualified(column: str | None, table: str) -> str | None:
    if not column:
        return None
    return column if "." in column else f"{table}.{column}"


def _filters(metric) -> list[str]:
    result = []
    for item in metric.filters or []:
        item = item.model_dump() if hasattr(item, "model_dump") else dict(item)
        item["column"] = _qualified(item["column"], metric.fact_table)
        item["operator"] = item["operator"].strip().upper()
        result.append(json.dumps(item, sort_keys=True, ensure_ascii=False))
    return sorted(result)


async def validate_metric_decomposition(metric, config: MetricDecomposition | None, repository) -> None:
    if config is None:
        return
    count, average = config.factors
    if average.source.numerator_metric_key != metric.metric_key:
        raise ValueError("拆解比值的分子必须引用当前目标指标")
    if metric.aggregation != "SUM":
        raise ValueError("第一版拆解仅支持 SUM 金额指标")
    source = await repository.get_metric_by_key(count.source.metric_key)
    if source is None or source.status != "published":
        raise ValueError("拆解引用的数量指标不存在或尚未发布")
    if source.aggregation not in {"COUNT", "COUNT_DISTINCT"}:
        raise ValueError("第一版数量因素必须为 COUNT 或 COUNT_DISTINCT 指标")
    if source.fact_table != metric.fact_table:
        raise ValueError("拆解因素必须来自相同事实表")
    if not metric.time_column or _qualified(source.time_column, source.fact_table) != _qualified(metric.time_column, metric.fact_table):
        raise ValueError("拆解因素必须使用相同的默认时间字段")
    if _filters(source) != _filters(metric):
        raise ValueError("拆解因素的固定筛选口径不一致")
