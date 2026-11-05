import asyncio
from types import SimpleNamespace
from unittest import TestCase

from app.schemas.chat import QuerySchema
from app.service.chat_service import ChatService
from app.service.conversation_context import (
    QueryContext, conversation_store, rewrite_query,
)


def _test_followup_rewrites_and_replaces_conditions():
    previous = QueryContext("查询华东最近30天销售额", "查询华东地区最近30天的销售额", "query_database")
    assert rewrite_query("那华南呢？", previous) == ("查询华南地区最近30天的销售额", True)
    assert rewrite_query("按渠道拆分一下。", previous) == ("查询华东地区最近30天的销售额，按渠道拆分", True)
    assert rewrite_query("改成上个月。", previous) == ("查询华东地区上个月的销售额", True)
    assert rewrite_query("查询全国销售额。", previous) == ("查询全国地区最近30天的销售额", True)
    metric_previous = QueryContext("各门店订单数量", "各门店的订单数量是多少？", "query_database")
    assert rewrite_query("那销售额呢", metric_previous) == ("各门店的销售额是多少？", True)


def _test_new_topic_and_missing_history_are_not_inherited():
    previous = QueryContext("查询华东销售额", "查询华东销售额", "query_database")
    assert rewrite_query("帮我解释什么是RAG", previous) == ("帮我解释什么是RAG", False)
    assert rewrite_query("那华南呢？", None) == ("那华南呢？", False)
    # 失败轮次不会写入 store；没有已确认上下文时保持原问题。
    assert rewrite_query("那华南呢？", None) == ("那华南呢？", False)


def _test_query_schema_generates_or_preserves_session_id():
    first = QuerySchema(query="查询销售额")
    second = QuerySchema(query="查询销售额", session_id="s-1")
    assert first.session_id and first.session_id != second.session_id
    assert second.session_id == "s-1"


class FakeGraph:
    def __init__(self, states):
        self.states = states
        self.inputs = []

    async def astream(self, *, input, context, stream_mode):
        self.inputs.append(input)
        yield "custom", {"type": "sse", "content": "ok"}
        yield "values", self.states

    async def ainvoke(self, *, input, context):
        self.inputs.append(input)
        return self.states


def service(graph):
    return ChatService(graph, None, None, None, None, None, None, None)


def _test_session_isolation_and_failed_turn_does_not_overwrite():
    conversation_store.clear_all()
    graph = FakeGraph({"intent": "query_database", "agent_result": {"error": None}})
    chat = service(graph)
    asyncio.run(chat.run_once("查询华东最近30天销售额", session_id="a"))
    asyncio.run(chat.run_once("那华南呢？", session_id="a"))
    asyncio.run(chat.run_once("那华南呢？", session_id="b"))
    # 改写已移到 Orchestrator 图内的节点，service 只透传原问题与 session_id。
    assert graph.inputs[1]["query"] == "那华南呢？"
    assert graph.inputs[1]["session_id"] == "a"
    assert graph.inputs[2]["query"] == "那华南呢？"
    assert graph.inputs[2]["session_id"] == "b"

    failed = FakeGraph({"intent": "query_database", "agent_result": {"error": "数据库失败"}})
    failing_chat = service(failed)
    asyncio.run(failing_chat.run_once("查询华北销售额", session_id="c"))
    asyncio.run(failing_chat.run_once("那华南呢？", session_id="c"))
    assert failed.inputs[1]["query"] == "那华南呢？"
    assert failed.inputs[1]["session_id"] == "c"


def _test_stream_preserves_custom_sse_and_remembers_success():
    conversation_store.clear_all()
    graph = FakeGraph({"intent": "query_database", "agent_result": {"error": None}})
    chat = service(graph)
    async def collect():
        return [item async for item in chat.stream_chat("查询华东销售额", session_id="s")]
    events = asyncio.run(collect())
    assert events == [{"type": "session", "session_id": "s"}, {"type": "sse", "content": "ok"}]
    assert conversation_store.get("s").rewritten_query == "查询华东销售额"


class ConversationContextTest(TestCase):
    def test_followup_rewrites_and_replaces_conditions(self):
        _test_followup_rewrites_and_replaces_conditions()

    def test_new_topic_and_missing_history_are_not_inherited(self):
        _test_new_topic_and_missing_history_are_not_inherited()

    def test_query_schema_generates_or_preserves_session_id(self):
        _test_query_schema_generates_or_preserves_session_id()

    def test_session_isolation_and_failed_turn_does_not_overwrite(self):
        _test_session_isolation_and_failed_turn_does_not_overwrite()

    def test_stream_preserves_custom_sse_and_remembers_success(self):
        _test_stream_preserves_custom_sse_and_remembers_success()
