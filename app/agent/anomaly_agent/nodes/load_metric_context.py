"""加载共享 Metadata 中的指标定义。

这个节点不访问事实数据，也不生成 SQL。它只把用户请求中的指标名称解析为
已经发布的业务指标定义，供后续 Planner 使用。
"""

from __future__ import annotations
from collections.abc import Mapping
from typing import Any

from langgraph.runtime import Runtime

from app.agent.anomaly_agent.schemas import (
    AnalysisRequest,
    MetricContext,
    MetricDimension,
)
from app.agent.anomaly_agent.state import AnomalyState
from app.agent.data_agent.context import DataAgentContext


def _as_list(value: Any) -> list[Any]:
    """将 Metadata JSON 字段安全地归一化为列表。"""

    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _build_dimensions(raw_dimensions: Any) -> list[MetricDimension]:
    """把 metric_info.dimensions 转成稳定的维度契约。"""

    dimensions: list[MetricDimension] = []
    for item in _as_list(raw_dimensions):
        if not isinstance(item, Mapping):
            continue
        column = str(item.get("column", "")).strip()
        name = str(item.get("name") or column).strip()
        if not column:
            continue
        aliases = [str(alias).strip() for alias in _as_list(item.get("aliases")) if str(alias).strip()]
        dimensions.append(MetricDimension(name=name, column=column, aliases=aliases))
    return dimensions


def _build_metric_context(metric_record: Any) -> MetricContext:
    """将 ORM 指标记录转换为 Agent 内部的数据契约。"""

    metric_key = str(getattr(metric_record, "metric_key", "") or "").strip()
    name = str(getattr(metric_record, "name", "") or "").strip()
    fact_table = str(getattr(metric_record, "fact_table", "") or "").strip()
    measure_column = str(getattr(metric_record, "measure_column", "") or "").strip()
    aggregation = str(getattr(metric_record, "aggregation", "") or "").strip()
    if not metric_key or not name or not fact_table or not measure_column or not aggregation:
        raise ValueError(f"指标 [{name or metric_key or '未知'}] 的 Metadata 定义不完整")

    raw_aliases = getattr(metric_record, "alias", None)
    aliases = [str(alias).strip() for alias in _as_list(raw_aliases) if str(alias).strip()]
    filters = [item for item in _as_list(getattr(metric_record, "filters", None)) if isinstance(item, Mapping)]

    return MetricContext(
        metric_key=metric_key,
        name=name,
        description=str(getattr(metric_record, "description", "") or ""),
        formula=str(getattr(metric_record, "formula", "") or ""),
        fact_table=fact_table,
        measure_column=measure_column,
        aggregation=aggregation,
        time_column=getattr(metric_record, "time_column", None),
        filters=[dict(item) for item in filters],
        dimensions=_build_dimensions(getattr(metric_record, "dimensions", None)),
        aliases=aliases,
    )


async def load_metric_context(
    state: AnomalyState,
    runtime: Runtime[DataAgentContext],
) -> dict[str, object]:
    """根据 AnalysisRequest.metric 查询并返回共享指标上下文。"""

    request: AnalysisRequest | None = state.get("analysis_request")
    if request is None:
        raise ValueError("加载指标上下文前必须先完成请求解析")

    repository = runtime.context.get("meta_mysql_repository")
    if repository is None:
        raise RuntimeError("运行时未注入 meta_mysql_repository，无法读取指标 Metadata")

    metric_record = await repository.get_metric_by_name(request.metric)
    if metric_record is None:
        raise ValueError(f"Metadata 中不存在已发布指标：{request.metric}")

    return {"metric_context": _build_metric_context(metric_record)}

