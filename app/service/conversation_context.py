"""Data-Agent 的轻量会话上下文与追问改写。

只保存最近一次成功的查询摘要，进程内按 session_id 隔离；不保存 SQL 结果、连接或完整消息历史。
"""
from __future__ import annotations

import re
import threading
from collections import deque
from dataclasses import dataclass
from uuid import uuid4


@dataclass(frozen=True)
class QueryContext:
    original_query: str
    rewritten_query: str
    intent: str
    filters: tuple[str, ...] = ()
    # 异动分析成功后的上一轮结构化请求，供追问改写补全指标/月份/筛选。
    analysis_request: dict | None = None


class ConversationStore:
    def __init__(self, max_sessions: int = 10_000):
        self._items: dict[str, QueryContext] = {}
        self._lock = threading.RLock()
        self._max_sessions = max_sessions

    def get(self, session_id: str) -> QueryContext | None:
        with self._lock:
            return self._items.get(session_id)

    def put(self, session_id: str, context: QueryContext) -> None:
        with self._lock:
            if len(self._items) >= self._max_sessions and session_id not in self._items:
                self._items.pop(next(iter(self._items)))
            self._items[session_id] = context

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._items.pop(session_id, None)

    def clear_all(self) -> None:
        with self._lock:
            self._items.clear()


conversation_store = ConversationStore()


class ConversationHistory:
    """按 session 保存最近 N 轮已改写问题的轻量内存历史。"""

    def __init__(self, max_turns: int = 6):
        self._items: dict[str, deque[str]] = {}
        self._lock = threading.RLock()
        self._max_turns = max_turns

    def append(self, session_id: str | None, content: str) -> None:
        """追加一轮已改写问题；空内容或空 session 直接忽略。"""
        if not session_id or not content:
            return
        with self._lock:
            turns = self._items.setdefault(session_id, deque(maxlen=self._max_turns))
            turns.append(content)

    def recent(self, session_id: str | None, limit: int = 6) -> list[str]:
        """返回最近若干轮问题，按时间从旧到新。"""
        if not session_id:
            return []
        with self._lock:
            turns = self._items.get(session_id)
            return list(turns)[-limit:] if turns else []

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._items.pop(session_id, None)


conversation_history = ConversationHistory()


# 仅处理明确的省略表达，未知表达保留原问题，避免臆造条件。
_FOLLOWUP_PREFIX = re.compile(r"^(?:那|那么|再|继续|另外|改成|换成|按|帮我|请)\s*")
_TOPIC_MARKERS = ("什么是", "解释", "怎么理解", "介绍", "定义", "RAG", "SQL 是什么")
_QUERY_MARKERS = ("查询", "查", "统计", "销售额", "订单", "金额", "数量", "按", "拆分", "地区", "区域", "最近", "上个月", "本月")
_REGIONS = "华东|华南|华北|华西|东北|西南|东南|中部"
_METRICS = ("销售额", "订单数量", "订单数", "销量", "利润", "客单价")
_ANOMALY_FOLLOWUP = re.compile(r"^(?:那|那么)\s*(.+?)(?:呢)$")
_ANOMALY_DIRECTIONS = {"decrease": "下降", "increase": "上升"}


def new_session_id() -> str:
    return uuid4().hex


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


def _rewrite_anomaly_followup(query: str, analysis_request: dict | None) -> tuple[str, bool]:
    """把“那北京呢”这类异动追问补全成完整问题；不是追问则原样返回。"""
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


def _is_followup(query: str, previous: QueryContext | None) -> bool:
    if previous is None or previous.intent != "query_database":
        return False
    text = query.strip()
    if any(marker.lower() in text.lower() for marker in _TOPIC_MARKERS):
        return False
    return bool(
        _FOLLOWUP_PREFIX.match(text)
        or text.rstrip("。！？?!").endswith(("呢", "一下", "吧"))
        or re.search(rf"(?:查询|统计)?\s*(?:{_REGIONS})(?:地区|区域)?", text)
        or "全国" in text
    ) and any(marker in text for marker in _QUERY_MARKERS + ("华东", "华南", "华北", "华西", "全国"))


def rewrite_query(query: str, previous: QueryContext | None) -> tuple[str, bool]:
    """返回 (送入 Orchestrator 的问题, 是否发生了安全改写)。"""
    text = query.strip()
    if previous is not None and previous.intent == "anomaly_analysis":
        return _rewrite_anomaly_followup(text, previous.analysis_request)
    if not _is_followup(text, previous):
        return text, False
    base = previous.rewritten_query

    # 明确替换地区条件；全国意味着移除上一轮地区过滤。
    if "全国" in text:
        rewritten = re.sub(rf"(?:{_REGIONS})(?:地区|区域)?", "全国地区", base, count=1)
        return rewritten, True

    # “那华南呢”替换旧地区；保留时间、指标和其他过滤。
    region = re.search(rf"({_REGIONS})(?:地区|区域)?", text)
    if region:
        old = re.compile(r"(?:华东|华南|华北|华西|东北|西南|东南|中部)(?:地区|区域)?")
        return old.sub(region.group(1) + "地区", base, count=1), True

    # “按渠道拆分”是在原查询上增加展示维度。
    dimension = re.search(r"按([\u4e00-\u9fffA-Za-z]+)(?:拆分|分组|看)", text)
    if dimension:
        suffix = f"，按{dimension.group(1)}拆分"
        return base if suffix in base else base + suffix, True

    # “改成上个月”替换时间范围。
    if "上个月" in text:
        rewritten = re.sub(r"最近\s*\d+\s*天|本月|上个月|近\d+天", "上个月", base)
        return rewritten, True

    # “那销售额呢”替换上一轮指标；只在识别到旧指标时改写，避免臆造。
    metric = next((item for item in _METRICS if item in text), None)
    old_metric = next((item for item in _METRICS if item in base), None)
    if metric and old_metric and metric != old_metric:
        return base.replace(old_metric, metric, 1), True

    return text, False


def infer_filters(query: str) -> tuple[str, ...]:
    regions = re.findall(r"华东|华南|华北|华西|东北|西南|东南|中部|全国", query)
    return tuple(dict.fromkeys(regions))
