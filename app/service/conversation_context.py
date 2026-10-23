"""Data-Agent 的轻量会话上下文与追问改写。

只保存最近一次成功的查询摘要，进程内按 session_id 隔离；不保存 SQL 结果、连接或完整消息历史。
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from uuid import uuid4


@dataclass(frozen=True)
class QueryContext:
    original_query: str
    rewritten_query: str
    intent: str
    filters: tuple[str, ...] = ()


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

# 仅处理明确的省略表达，未知表达保留原问题，避免臆造条件。
_FOLLOWUP_PREFIX = re.compile(r"^(?:那|那么|再|继续|另外|改成|换成|按|帮我|请)\s*")
_TOPIC_MARKERS = ("什么是", "解释", "怎么理解", "介绍", "定义", "RAG", "SQL 是什么")
_QUERY_MARKERS = ("查询", "查", "统计", "销售额", "订单", "金额", "数量", "按", "拆分", "地区", "区域", "最近", "上个月", "本月")
_REGIONS = "华东|华南|华北|华西|东北|西南|东南|中部"
_METRICS = ("销售额", "订单数量", "订单数", "销量", "利润", "客单价")


def new_session_id() -> str:
    return uuid4().hex


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
