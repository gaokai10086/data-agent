"""图表决策节点：规则优先选择图表，LLM 补充展示语义。

第一层规则：
    明确的数据形状直接决策，保证稳定与可解释。

第二层 LLM：
    多维度、多指标等复杂场景由模型选择图表；简单场景也由模型补充
    指标类型、单位、数值尺度和坐标轴等展示语义。
"""
import json
from typing import Any, Dict

from langgraph.runtime import Runtime

from app.agent.data_agent.context import DataAgentContext
from app.core.llm import get_context_llm
from app.agent.data_agent.state import DataAgentState
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
    "年",
    "月",
    "日",
    "日期",
    "时间",
    "季度",
    "周",
)

ALLOWED_CHART_TYPES = frozenset({"line", "bar", "pie", "table"})
ALLOWED_DISPLAY_TYPES = frozenset({"number", "count", "amount", "percentage", "ratio", "duration"})
ALLOWED_VALUE_SCALES = frozenset({"absolute", "fraction", "percent"})
ALLOWED_AXIS = frozenset({"left", "right"})


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

    # 时间 + 分类维度 + 单指标：时间作为 X 轴，分类维度拆成多条系列。
    # 例如「按月查看华东和华中销售额」应生成两条线，而不是把两组数据串成一条线。
    if len(dimensions) == 2 and len(metrics) == 1:
        time_dimension = next(
            (dimension for dimension in dimensions if any(hint in dimension.lower() for hint in TIME_HINTS)),
            None,
        )
        if time_dimension:
            series_dimension = next(dimension for dimension in dimensions if dimension != time_dimension)
            return {
                "chart_type": "line",
                "x_column": time_dimension,
                "y_columns": [metrics[0]],
                "series_dimension": series_dimension,
                "reason": "时间维度作为横轴，按分类维度拆分为多条趋势线。",
            }
        return {
            "chart_type": "bar",
            "x_column": dimensions[0],
            "y_columns": [metrics[0]],
            "series_dimension": dimensions[1],
            "reason": "双分类维度适合使用分组柱状图进行对比。",
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


def _normalize_metric_semantics(payload: Any, analysis: dict[str, Any]) -> dict[str, dict[str, str | None]]:
    """校验 LLM 返回的展示语义，避免模型直接修改查询数值。"""
    if not isinstance(payload, dict):
        return {}
    metrics = {str(metric) for metric in (analysis.get("metrics") or [])}
    normalized: dict[str, dict[str, str | None]] = {}
    for metric, raw in payload.items():
        if str(metric) not in metrics or not isinstance(raw, dict):
            continue
        display_type = str(raw.get("display_type") or "number").lower()
        value_scale = str(raw.get("value_scale") or "absolute").lower()
        axis = str(raw.get("axis") or "left").lower()
        if display_type not in ALLOWED_DISPLAY_TYPES:
            display_type = "number"
        if value_scale not in ALLOWED_VALUE_SCALES:
            value_scale = "absolute"
        if axis not in ALLOWED_AXIS:
            axis = "right" if display_type in {"percentage", "ratio"} else "left"
        if display_type in {"percentage", "ratio"}:
            axis = "right"
        normalized[str(metric)] = {
            "display_type": display_type,
            "unit": str(raw.get("unit") or "")[:16],
            "value_scale": value_scale,
            "axis": axis,
        }
    return normalized


def _build_llm_prompt(
    query: str,
    analysis: dict[str, Any],
    rows: list[dict[str, Any]],
    rule_plan: dict[str, Any] | None = None,
) -> str:
    columns = analysis.get("columns") or []
    dimensions = analysis.get("dimensions") or []
    metrics = analysis.get("metrics") or []
    row_count = analysis.get("row_count") or 0
    sample = rows[:5]
    rule_hint = json.dumps(rule_plan, ensure_ascii=False) if rule_plan else "无，需根据结果结构自行选择。"
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

【规则层建议】
{rule_hint}

【要求】
1. 只能从 line、bar、pie、table 中选择 chart_type；如果存在规则层建议，应保留其图表选择和字段，只补充 metric_semantics。
2. x_column 必须是结果中真实存在的维度字段。
3. y_columns 必须是结果中真实存在的指标字段，且不能为空（table 可空）。
4. 只在适合绘图时选择 line/bar/pie；数据不适合绘图时选择 table。
5. 如果结果包含两个维度和一个指标，优先设置 series_dimension，将第二个维度拆成多条系列；时间序列应将时间字段设置为 x_column。
6. 同时为每个指标返回 metric_semantics，只返回展示语义，不要修改 data 中的原始值。
7. 输出严格 JSON，不要 Markdown。

输出格式：
{{
  "chart_type": "bar",
  "x_column": "region",
  "y_columns": ["sales_amount"],
  "series_dimension": null,
  "metric_semantics": {{
    "sales_amount": {{"display_type": "amount", "unit": "元", "value_scale": "absolute", "axis": "left"}}
  }},
  "reason": "适合按区域对比销售额"
}}"""


async def _llm_plan(
    query: str,
    analysis: dict[str, Any],
    rows: list[dict[str, Any]],
    runtime: Runtime[DataAgentContext],
    rule_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """调用轻量 LLM 辅助判断复杂场景。"""
    prompt = _build_llm_prompt(query, analysis, rows, rule_plan)
    llm = get_context_llm(runtime, temperature=0.0)
    response = await llm.ainvoke(prompt)
    payload = _extract_json(response.content if hasattr(response, "content") else str(response))

    chart_type = str(payload.get("chart_type") or "table").lower()
    if chart_type not in ALLOWED_CHART_TYPES:
        chart_type = "table"

    x_column = payload.get("x_column")
    y_columns = payload.get("y_columns") or []
    series_dimension = payload.get("series_dimension")
    if not isinstance(y_columns, list):
        y_columns = []
    if series_dimension is not None:
        series_dimension = str(series_dimension)
        if series_dimension not in (analysis.get("dimensions") or []):
            series_dimension = None

    return {
        "chart_type": chart_type,
        "x_column": x_column,
        "y_columns": [str(item) for item in y_columns],
        "series_dimension": series_dimension,
        "metric_semantics": _normalize_metric_semantics(payload.get("metric_semantics"), analysis),
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

    rule_plan = _rule_plan(analysis)

    try:
        # 一次调用同时完成“图表决策 + 指标展示语义推断”。
        llm_plan = await _llm_plan(
            state.get("query", ""),
            analysis,
            rows,
            runtime,
            rule_plan,
        )
        if rule_plan is not None:
            # 简单场景继续信任确定性规则，LLM 只补充展示语义，避免模型改变已确定的图表结构。
            plan = dict(rule_plan)
            plan["metric_semantics"] = llm_plan.get("metric_semantics") or {}
            plan["source"] = "rule+llm_semantics"
        else:
            plan = llm_plan
            plan["source"] = "llm"
        logger.info(
            "🤖 [ChartDecision] 图表决策完成: type={}, source={}",
            plan.get("chart_type", "table"),
            plan.get("source", "unknown"),
        )
        logger.debug("[ChartDecision] plan={}", plan)
    except Exception as exc:
        if rule_plan is not None:
            logger.warning(f"⚠️ [ChartDecision] 展示语义推断失败，保留规则图表：{exc}")
            plan = dict(rule_plan)
            plan["metric_semantics"] = {}
            plan["source"] = "rule"
        else:
            logger.warning(f"⚠️ [ChartDecision] LLM 决策失败，回退 table：{exc}")
            plan = {
                "chart_type": "table",
                "x_column": None,
                "y_columns": [],
                "metric_semantics": {},
                "reason": f"LLM 判断失败，回退表格：{exc}",
                "source": "fallback",
            }

    return {"chart_plan": plan}
