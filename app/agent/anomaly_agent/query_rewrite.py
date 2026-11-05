"""异动分析的多轮追问改写。

基于上一轮结构化 AnalysisRequest 重建一条完整、可被 parse_request 解析的
问题。当前只覆盖“换城市 / 换渠道”两类高频追问，其余保持原样不臆造。
"""
from __future__ import annotations

import re

_ANOMALY_FOLLOWUP = re.compile(r"^(?:那|那么)\s*(.+?)(?:呢)$")
_ANOMALY_DIRECTIONS = {"decrease": "下降", "increase": "上升"}


def _format_period(period: str) -> str:
    year, month = period.split("-", 1)
    return f"{year}年{int(month)}月"


def _rebuild_anomaly_question(request: dict, filters: list[dict]) -> str:
    """用上一轮结构化请求重建一条可解析的完整问题。"""
    metric = request.get("metric", "销售额")
    target = _format_period(request.get("target_period", ""))
    comparison = _format_period(request.get("comparison_period", ""))
    direction = _ANOMALY_DIRECTIONS.get(request.get("expected_direction"), "变化")
    scope = ""
    for item in filters:
        dimension = item.get("dimension")
        value = item.get("value", "")
        if not value:
            continue
        if dimension == "城市":
            scope += value
        elif dimension:
            scope += f"{value}{dimension}"
        else:
            scope += value
    return f"为什么{target}{scope}{metric}比{comparison}{direction}了？"


def rewrite_anomaly_followup(query: str, analysis_request: dict | None) -> tuple[str, bool]:
    """把“那北京呢”这类追问补全成完整问题；不是追问则原样返回。"""
    if not analysis_request:
        return query, False
    text = query.strip()
    match = _ANOMALY_FOLLOWUP.match(text)
    if not match:
        return text, False
    target = match.group(1).strip()
    if not target:
        return text, False

    channel = re.search(r"(线上|线下)", target)
    if channel:
        dimension, value = "渠道", channel.group(1)
    else:
        value = re.sub(r"(?:地区|区域|大区|省)$", "", target)
        dimension = "城市"

    filters = [dict(item) for item in (analysis_request.get("filters") or [])]
    replaced = False
    geo_dimensions = ("城市", "省份", "大区", "地区")
    for item in filters:
        if dimension == "城市" and item.get("dimension") in geo_dimensions:
            item["dimension"], item["value"] = "城市", value
            replaced = True
            break
        if dimension != "城市" and item.get("dimension") == dimension:
            item["value"] = value
            replaced = True
            break
    if not replaced:
        filters.append({"dimension": dimension, "value": value, "operator": "="})

    return _rebuild_anomaly_question(analysis_request, filters), True
