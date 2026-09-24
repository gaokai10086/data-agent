"""根据图表决策生成最终图表配置，供前端渲染。"""
from typing import Any, Dict

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.logging import logger


def _build_options(chart_type: str) -> dict[str, Any]:
    if chart_type == "line":
        return {"xAxis": {"type": "category"}, "yAxis": {"type": "value"}, "smooth": True}
    if chart_type == "bar":
        return {"xAxis": {"type": "category"}, "yAxis": {"type": "value"}}
    if chart_type == "pie":
        return {"radius": "60%"}
    return {}


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
    config = {
        "chart_type": chart_type,
        # 标题不在这里生成：这里拿不到中文字段映射，
        # 由 present_result 统一用中文字段名拼装规范标题。
        "title": "",
        "x_column": decision.get("x_column"),
        "y_columns": decision.get("y_columns") or [],
        "data": rows,
        "options": _build_options(chart_type),
    }
    logger.info(f"🎨 [ChartGenerate] 图表配置生成完成：{chart_type}")
    return {"chart_config": config}
