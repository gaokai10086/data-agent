"""图表决策节点：规则优先，规则无法判断时调用 LLM 辅助。

第一层规则：
    明确的数据形状直接决策，保证稳定与可解释。

第二层 LLM：
    多维度、多指标等规则覆盖不到的复杂场景，交给轻量模型语义理解。
"""
import json
from typing import Any, Dict

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import get_llm
from app.agent.state import DataAgentState
from app.core.logging import logger

TIME_HINTS = (
    "date",
    "time",
    "year",
    "month",
    "day",
    "quarter",
    "week",
    "pay_time",
    "created_time",
)

ALLOWED_CHART_TYPES = frozenset({"line", "bar", "pie", "table"})


def _rule_plan(analysis: dict[str, Any]) -> dict[str, Any] | None:
    """第一层规则决策。返回 None 表示规则无法确定，需要 LLM。"""
    row_count = int(analysis.get("row_count") or 0)
    dimensions = [str(item) for item in (analysis.get("dimensions") or [])]
    metrics = [str(item) for item in (analysis.get("metrics") or [])]

    if row_count <= 1:
        return {
            "chart_type": "table",
            "x_column": None,
            "y_columns": [],
            "reason": "数据行数不足以绘制图表。",
        }

    if not dimensions:
        return {
            "chart_type": "table",
            "x_column": None,
            "y_columns": [],
            "reason": "结果中没有明确维度，表格更合适。",
        }

    if len(dimensions) == 1 and len(metrics) == 1:
        x_column = dimensions[0]
        y_columns = [metrics[0]]
        lowered = x_column.lower()
        if any(hint in lowered for hint in TIME_HINTS):
            return {
                "chart_type": "line",
                "x_column": x_column,
                "y_columns": y_columns,
                "reason": "单一维度且疑似时间序列，适合折线图。",
            }
        if row_count <= 5:
            return {
                "chart_type": "pie",
                "x_column": x_column,
                "y_columns": y_columns,
                "reason": "低基数维度与单一指标，适合饼图展示占比。",
            }
        return {
            "chart_type": "bar",
            "x_column": x_column,
            "y_columns": y_columns,
            "reason": "单一维度对应指标，适合柱状图对比。",
        }

    # 多维、多指标等复杂场景交给 LLM
    return None


def _extract_json(raw_text: str) -> dict[str, Any]:
    text = (raw_text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError(f"LLM 输出无法解析为 JSON：{raw_text[:200]}")
        return json.loads(text[start : end + 1])


def _build_llm_prompt(
    query: str,
    analysis: dict[str, Any],
    rows: list[dict[str, Any]],
) -> str:
    columns = analysis.get("columns") or []
    dimensions = analysis.get("dimensions") or []
    metrics = analysis.get("metrics") or []
    row_count = analysis.get("row_count") or 0
    sample = rows[:5]
    return f"""你是一个数据分析与可视化专家。请根据用户问题、SQL 结果的结构和样本数据，选择最合适的图表类型。

【用户问题】
{query}

【结果结构】
字段列表：{columns}
维度字段：{dimensions}
指标字段：{metrics}
行数：{row_count}

【前几行样本】
{json.dumps(sample, ensure_ascii=False, default=str)}

【要求】
1. 只能从 line、bar、pie、table 中选择 chart_type。
2. x_column 必须是结果中真实存在的维度字段。
3. y_columns 必须是结果中真实存在的指标字段，且不能为空（table 可空）。
4. 只在适合绘图时选择 line/bar/pie；数据不适合绘图时选择 table。
5. 输出严格 JSON，不要 Markdown。

输出格式：
{{
  "chart_type": "bar",
  "x_column": "region",
  "y_columns": ["sales_amount"],
  "reason": "适合按区域对比销售额"
}}"""


async def _llm_plan(
    query: str,
    analysis: dict[str, Any],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """调用轻量 LLM 辅助判断复杂场景。"""
    prompt = _build_llm_prompt(query, analysis, rows)
    llm = get_llm(provider="deepseek", model_tier="flash", temperature=0.0)
    response = await llm.ainvoke(prompt)
    payload = _extract_json(response.content if hasattr(response, "content") else str(response))

    chart_type = str(payload.get("chart_type") or "table").lower()
    if chart_type not in ALLOWED_CHART_TYPES:
        chart_type = "table"

    x_column = payload.get("x_column")
    y_columns = payload.get("y_columns") or []
    if not isinstance(y_columns, list):
        y_columns = []

    return {
        "chart_type": chart_type,
        "x_column": x_column,
        "y_columns": [str(item) for item in y_columns],
        "reason": str(payload.get("reason") or "LLM 根据结果语义辅助判断。"),
    }


async def chart_decision(
    state: DataAgentState,
    runtime: Runtime[DataAgentContext],
) -> Dict[str, Any]:
    writer = getattr(runtime, "stream_writer", None)
    if writer:
        writer({"stage": "决策图表类型"})

    analysis = state.get("result_analysis") or {}
    rows = state.get("sql_result") or []
    if not isinstance(rows, list):
        rows = []

    plan = _rule_plan(analysis)
    if plan is not None:
        plan["source"] = "rule"
        logger.info(f"📊 [ChartDecision] 规则决策：{plan}")
        return {"chart_plan": plan}

    try:
        plan = await _llm_plan(state.get("query", ""), analysis, rows)
        plan["source"] = "llm"
        logger.info(f"🤖 [ChartDecision] LLM 辅助决策：{plan}")
    except Exception as exc:
        logger.warning(f"⚠️ [ChartDecision] LLM 决策失败，回退 table：{exc}")
        plan = {
            "chart_type": "table",
            "x_column": None,
            "y_columns": [],
            "reason": f"LLM 判断失败，回退表格：{exc}",
            "source": "fallback",
        }

    return {"chart_plan": plan}
