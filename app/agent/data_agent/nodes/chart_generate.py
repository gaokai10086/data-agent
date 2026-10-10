"""根据图表决策生成最终图表配置，供前端渲染。"""
from typing import Any, Dict

from langgraph.runtime import Runtime

from app.agent.data_agent.context import DataAgentContext
from app.agent.data_agent.state import DataAgentState
from app.core.logging import logger

ALLOWED_CHART_TYPES = frozenset({"line", "bar", "pie", "table"})


def _build_options(chart_type: str) -> dict[str, Any]:
    if chart_type == "line":
        return {"xAxis": {"type": "category"}, "yAxis": {"type": "value"}, "smooth": True}
    if chart_type == "bar":
        return {"xAxis": {"type": "category"}, "yAxis": {"type": "value"}}
    if chart_type == "pie":
        return {"radius": "60%"}
    return {}


def _build_table_config(rows: list[dict[str, Any]], reason: str) -> dict[str, Any]:
    """生成安全的表格降级配置，保证前端至少能展示原始结果。"""
    return {
        "chart_type": "table",
        "title": "",
        "x_column": None,
        "y_columns": [],
        "data": rows,
        "options": {},
        "presentation": {},
        "reason": reason,
        "source": "fallback",
    }


async def chart_generate(
    state: DataAgentState,
    runtime: Runtime[DataAgentContext],
) -> Dict[str, Any]:
    writer = getattr(runtime, "stream_writer", None)
    if writer:
        writer({"stage": "生成图表配置"})

    decision = state.get("chart_plan") or {}
    rows = state.get("sql_result") or []
    if not isinstance(rows, list):
        rows = []

    chart_type = decision.get("chart_type") or "table"
    columns = set(rows[0].keys()) if rows else set()
    metric_columns = set((state.get("result_analysis") or {}).get("metrics") or [])
    x_column = decision.get("x_column")
    y_columns = decision.get("y_columns") or []
    series_dimension = decision.get("series_dimension")
    if not isinstance(y_columns, list):
        y_columns = []

    if chart_type not in ALLOWED_CHART_TYPES:
        config = _build_table_config(rows, f"不支持的图表类型：{chart_type}")
    elif chart_type != "table" and (
        not x_column or x_column not in columns or
        not y_columns or any(column not in columns for column in y_columns) or
        (metric_columns and any(column not in metric_columns for column in y_columns)) or
        (series_dimension is not None and series_dimension not in columns)
    ):
        config = _build_table_config(rows, "图表字段不存在于查询结果，降级为表格")
    else:
        config = {
            "chart_type": chart_type,
            # 标题不在这里生成：这里拿不到中文字段映射，
            # 由 present_result 统一用中文字段名拼装规范标题。
            "title": "",
            "x_column": x_column,
            "y_columns": [str(column) for column in y_columns],
            "series_dimension": series_dimension,
            "metric_semantics": decision.get("metric_semantics") or {},
            "data": rows,
            "options": _build_options(chart_type),
            "presentation": decision.get("presentation") or {},
            "reason": decision.get("reason", ""),
            "source": decision.get("source", "rule"),
        }
    logger.info(f"🎨 [ChartGenerate] 图表配置生成完成：{chart_type}")
    return {"chart_config": config}
