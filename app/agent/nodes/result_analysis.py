"""分析 SQL 结果：识别维度、指标和行数，为图表决策提供结构化依据。"""
from decimal import Decimal
from typing import Any, Dict

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.logging import logger


def _is_numeric(value: Any) -> bool:
    # MySQL 的 DECIMAL 列返回 Decimal，必须一并识别为数值，否则会被误判成维度。
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


async def result_analysis(
    state: DataAgentState,
    runtime: Runtime[DataAgentContext],
) -> Dict[str, Any]:
    writer = getattr(runtime, "stream_writer", None)
    if writer:
        writer({"stage": "分析查询结果"})

    rows = state.get("sql_result")
    if not isinstance(rows, list) or not rows:
        analysis = {
            "summary": "SQL 未返回结构化数据。",
            "columns": [],
            "metrics": [],
            "dimensions": [],
            "row_count": 0,
        }
        logger.info(f"✨ [ResultAnalysis] 结果为空：{analysis}")
        return {"result_analysis": analysis}

    columns = list(rows[0].keys())
    metrics: list[str] = []
    dimensions: list[str] = []

    for column in columns:
        sample = next((row.get(column) for row in rows if row.get(column) is not None), None)
        if _is_numeric(sample):
            metrics.append(column)
        else:
            dimensions.append(column)

    analysis = {
        "summary": (
            f"共 {len(rows)} 行、{len(columns)} 列；"
            f"维度 {len(dimensions)} 个，指标 {len(metrics)} 个。"
        ),
        "columns": columns,
        "metrics": metrics,
        "dimensions": dimensions,
        "row_count": len(rows),
    }
    logger.info(f"✨ [ResultAnalysis] 结果分析完成：{analysis}")
    return {"result_analysis": analysis}
