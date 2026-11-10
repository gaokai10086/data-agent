"""基于已登记 Meta 维度和值证据完成筛选条件 grounding。"""

from __future__ import annotations

import re
from typing import Any

from app.agent.anomaly_agent.schemas import FilterCondition, TaskRequest
from app.core.meta.value_grounding import match_value_expression


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else ([] if value is None else [value])


def _query_variants(value: str) -> list[str]:
    """只移除通用行政表达，最终仍由完整用户表达匹配证据。"""
    value = value.strip()
    variants = [value]
    stripped = re.sub(r"(?:地区|地域|市)$", "", value).strip()
    if stripped and stripped != value:
        variants.append(stripped)
    return list(dict.fromkeys(variants))


async def ground_task_filters(task: TaskRequest, raw_question: str, runtime: Any) -> TaskRequest:
    """将无字段筛选绑定到唯一的指标维度和值证据；无法唯一确定则保留 None。"""
    filters = list(task.filters)
    ungrounded = [item for item in filters if item.dimension is None]
    if not ungrounded:
        return task

    context = getattr(runtime, "context", {}) or {}
    meta_repository = context.get("meta_mysql_repository")
    value_repository = context.get("value_es_repository")
    metric_name = getattr(task, "metric", None)
    if not metric_name:
        metrics = getattr(task, "metrics", [])
        metric_name = metrics[0] if len(metrics) == 1 else None
    if meta_repository is None or value_repository is None or not metric_name:
        return task

    metric = await meta_repository.get_metric_by_name(metric_name)
    if metric is None:
        return task
    dimensions = [item for item in _as_list(getattr(metric, "dimensions", None)) if isinstance(item, dict)]

    grounded: list[FilterCondition] = []
    for condition in filters:
        if condition.dimension is not None:
            grounded.append(condition)
            continue

        evidence: list[dict] = []
        for variant in _query_variants(condition.value):
            try:
                evidence.extend(await value_repository.query(variant, limit=20))
            except Exception:
                # 值召回不可用时不猜测字段，保留安全拒绝路径。
                continue

        allowed_columns = {
            str(item.get("column", "")).strip(): item
            for item in dimensions
            if str(item.get("column", "")).strip()
        }
        matched = []
        seen = set()
        for item in evidence:
            column = f"{item.get('table_name')}.{item.get('column_name')}"
            if column not in allowed_columns:
                continue
            if match_value_expression(raw_question, item) is None:
                continue
            key = (column, str(item.get("value") or "").strip())
            if key not in seen:
                seen.add(key)
                matched.append(item)

        columns = {f"{item.get('table_name')}.{item.get('column_name')}" for item in matched}
        values = {str(item.get("value") or "").strip() for item in matched if item.get("value")}
        if len(columns) == 1 and len(values) == 1:
            column = next(iter(columns))
            dimension = allowed_columns[column]
            grounded.append(
                condition.model_copy(
                    update={
                        "dimension": str(dimension.get("name") or column.rsplit(".", 1)[-1]),
                        "value": next(iter(values)),
                    }
                )
            )
        else:
            grounded.append(condition)

    return task.model_copy(update={"filters": grounded})
