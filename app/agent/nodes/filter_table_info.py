"""确定性表结构压缩：保留召回强证据与关键字段，去掉无关字段。"""
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.logging import logger

KEY_ROLES = {"primary_key", "foreign_key"}


def _protect(protected: dict[str, set[str]], table_name: str, column_name: str) -> None:
    if table_name and column_name:
        protected.setdefault(table_name, set()).add(column_name)


def _parse_column_reference(reference: str, fact_table: str) -> tuple[str, str]:
    if "." in reference:
        table_name, column_name = reference.split(".", 1)
        return table_name.strip(), column_name.strip()
    return fact_table.strip(), reference.strip()


def _protect_metric(protected: dict[str, set[str]], metric: dict) -> None:
    fact_table = str(metric.get("fact_table") or "").strip()
    measure_column = str(metric.get("measure_column") or "").strip()
    time_column = str(metric.get("time_column") or "").strip()
    _protect(protected, fact_table, measure_column)
    _protect(protected, fact_table, time_column)

    for item in metric.get("filters") or []:
        if isinstance(item, dict) and item.get("column"):
            table_name, column_name = _parse_column_reference(str(item["column"]), fact_table)
            _protect(protected, table_name, column_name)

    for item in metric.get("dimensions") or []:
        if isinstance(item, dict) and item.get("column"):
            table_name, column_name = _parse_column_reference(str(item["column"]), fact_table)
            _protect(protected, table_name, column_name)


def _protect_values(protected: dict[str, set[str]], values: list[dict]) -> None:
    for value in values or []:
        _protect(
            protected,
            str(value.get("table_name") or ""),
            str(value.get("column_name") or ""),
        )


def _protect_relations(protected: dict[str, set[str]], relations: list[dict]) -> None:
    for relation in relations or []:
        _protect(protected, str(relation.get("source_table") or ""), str(relation.get("source_column") or ""))
        _protect(protected, str(relation.get("target_table") or ""), str(relation.get("target_column") or ""))


async def filter_table_info(
    state: DataAgentState,
    runtime: Runtime[DataAgentContext],
) -> dict:
    writer = getattr(runtime, "stream_writer", None)
    if writer:
        writer({"stage": "压缩候选表结构"})

    table_infos = state.get("table_infos", [])
    retrieved_values = state.get("retrieved_values", [])
    metric_infos = state.get("metric_infos", [])
    table_relations = state.get("table_relations", [])

    protected: dict[str, set[str]] = {}
    for metric in metric_infos:
        _protect_metric(protected, metric)
    _protect_values(protected, retrieved_values)
    _protect_relations(protected, table_relations)

    # 没有强证据时不做激进压缩，保持原有候选，避免把有效 Schema 裁空。
    if not protected:
        logger.info("ℹ️ [FilterTable] 未发现强保护字段，保持候选表结构不变")
        return {"table_infos": table_infos}

    compressed: list[dict] = []
    for table in table_infos:
        table_name = str(table.get("name") or "")
        protected_columns = protected.get(table_name, set())
        columns = [
            column
            for column in table.get("columns", [])
            if column.get("name") in protected_columns or column.get("role") in KEY_ROLES
        ]
        if columns:
            new_table = dict(table)
            new_table["columns"] = columns
            compressed.append(new_table)

    logger.info(
        "📋 [FilterTable] 压缩候选表结构，保留表："
        + "、".join(str(table.get("name")) for table in compressed)
    )
    return {"table_infos": compressed}
