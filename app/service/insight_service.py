"""业务洞察服务：将事实计算与 LLM 总结解耦。"""
from decimal import Decimal
from typing import Any


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_number(value: float) -> str:
    return f"{int(value):,}" if float(value).is_integer() else f"{value:,.2f}"


def _display_name(name: str) -> str:
    aliases = {
        "month": "月份", "date": "日期", "day": "日期", "region": "大区",
        "channel": "渠道", "store_name": "门店", "category": "品类",
        "platform": "平台", "sales_amount": "销售额", "order_count": "订单数",
        "user_count": "用户数",
    }
    return aliases.get(name.lower(), name)


class InsightService:
    """只负责事实计算和 Prompt 构建，不直接调用模型。"""

    @staticmethod
    def build_facts(rows: list[dict[str, Any]], analysis: dict[str, Any]) -> list[dict[str, Any]]:
        """根据 SQL 结果计算结构化事实。"""
        facts: list[dict[str, Any]] = []
        if not rows:
            facts.append({"type": "empty", "title": "数据行数", "value": 0})
            return facts

        facts.append({"type": "row_count", "title": "数据行数", "value": len(rows)})

        metrics = [str(item) for item in (analysis.get("metrics") or [])]
        dimensions = [str(item) for item in (analysis.get("dimensions") or [])]

        for metric in metrics:
            values = [_to_float(row.get(metric)) for row in rows if _to_float(row.get(metric)) is not None]
            if not values:
                continue
            facts.extend([
                {"type": "metric_sum", "title": f"{metric} 总和", "value": round(sum(values), 2)},
                {"type": "metric_avg", "title": f"{metric} 平均", "value": round(sum(values) / len(values), 2)},
                {"type": "metric_min", "title": f"{metric} 最小", "value": round(min(values), 2)},
                {"type": "metric_max", "title": f"{metric} 最大", "value": round(max(values), 2)},
            ])

        for dimension in dimensions:
            distinct_values = {str(row.get(dimension)) for row in rows if row.get(dimension) is not None}
            facts.append({
                "type": "dimension_count",
                "title": f"{dimension} 去重数量",
                "value": len(distinct_values),
            })

        return facts

    @staticmethod
    def build_kpis(rows: list[dict[str, Any]], analysis: dict[str, Any]) -> list[dict[str, Any]]:
        """根据结果形状生成业务 KPI，避免固定展示 sum/avg/min/max。"""
        if not rows:
            return []
        metrics = [str(item) for item in (analysis.get("metrics") or [])]
        dimensions = [str(item) for item in (analysis.get("dimensions") or [])]
        if not metrics:
            return []
        metric = metrics[0]
        metric_label = _display_name(metric)
        numeric_rows = [
            (row, _to_float(row.get(metric)))
            for row in rows
            if _to_float(row.get(metric)) is not None
        ]
        if not numeric_rows:
            return []
        values = [value for _, value in numeric_rows]
        total = round(sum(values), 2)
        kpis: list[dict[str, Any]] = []

        if not dimensions:
            value = values[0] if len(values) == 1 else total
            kpis.append({"label": metric_label, "value": round(value, 2), "unit": "", "priority": 1})
            return kpis

        dimension = dimensions[0]
        ranked = sorted(numeric_rows, key=lambda item: item[1], reverse=True)
        top_row, top_value = ranked[0]
        bottom_row, bottom_value = ranked[-1]
        dimension_label = _display_name(dimension)
        kpis.append({"label": f"{metric_label}总量", "value": total, "unit": "", "priority": 1})
        kpis.append({"label": f"最高{dimension_label}", "value": str(top_row.get(dimension)),
                     "description": f"{_format_number(top_value)}", "priority": 2})
        kpis.append({"label": f"最低{dimension_label}", "value": str(bottom_row.get(dimension)),
                     "description": f"{_format_number(bottom_value)}", "priority": 3})
        if bottom_value not in (0, None) and top_value is not None:
            kpis.append({"label": "最高/最低", "value": round(top_value / bottom_value, 1),
                         "unit": "倍", "priority": 4})
        return kpis[:4]

    @staticmethod
    def build_prompt(
        query: str,
        analysis: dict[str, Any],
        facts: list[dict[str, Any]],
        rows: list[dict[str, Any]],
    ) -> str:
        """构建业务洞察生成 Prompt。"""
        import json

        columns = analysis.get("columns") or []
        dimensions = analysis.get("dimensions") or []
        metrics = analysis.get("metrics") or []
        row_count = analysis.get("row_count") or 0
        sample = rows[:5]
        return f"""你是一个资深数据分析师。请根据用户问题、SQL 查询结果的事实统计，生成一段简洁、可读的业务洞察摘要。

【用户问题】
{query}

【结果结构】
字段列表：{columns}
维度字段：{dimensions}
指标字段：{metrics}
行数：{row_count}

【事实统计】
{json.dumps(facts, ensure_ascii=False, default=str)}

【前几行样本】
{json.dumps(sample, ensure_ascii=False, default=str)}

【要求】
1. 用中文输出，不超过 150 字。
2. 只基于给定事实，不编造未提供的数据。
3. 优先说明主要趋势、对比或业务含义。
4. 直接输出一段纯文本，不要 Markdown 标题或列表符号。"""
