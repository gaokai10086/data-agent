"""Data-Agent 多轮上下文的可重复 Mock 测试。

可用 pytest 运行；当前环境没有安装 pytest 时，也可用 unittest 运行这些 TestCase。
"""
from __future__ import annotations

import asyncio
from unittest import TestCase

from app.schemas.chat import QuerySchema
from app.service.chat_service import ChatService
from app.service.conversation_context import QueryContext, conversation_store, rewrite_query


def context(query: str) -> QueryContext:
    return QueryContext(query, query, "query_database")


class FakeGraph:
    def __init__(self, state=None):
        self.state = state or {"intent": "query_database", "agent_result": {"error": None}}
        self.inputs = []

    async def ainvoke(self, *, input, context):
        self.inputs.append(input)
        return self.state

    async def astream(self, *, input, context, stream_mode):
        self.inputs.append(input)
        yield "custom", {"type": "bi_report", "content": "ok"}
        yield "values", self.state


def make_service(graph):
    return ChatService(graph, None, None, None, None, None, None, None)


class MultiTurnRewriteTests(TestCase):
    def setUp(self):
        conversation_store.clear_all()

    def test_1_region_replacement_does_not_keep_old_region(self):
        previous = context("查询最近30天华东地区的销售额")
        actual, changed = rewrite_query("那华南地区呢？", previous)
        self.assertTrue(changed)
        self.assertIn("华南", actual)
        self.assertNotIn("华东", actual)
        self.assertIn("最近30天", actual)
        self.assertIn("销售额", actual)

    def test_2_time_replacement_preserves_region_and_metric(self):
        actual, changed = rewrite_query(
            "改成上个月呢？", context("查询华东地区最近30天的订单数量")
        )
        self.assertTrue(changed)
        self.assertIn("上个月", actual)
        self.assertIn("华东", actual)
        self.assertIn("订单数量", actual)
        self.assertNotIn("最近30天", actual)

    def test_3_group_dimension_is_added(self):
        actual, changed = rewrite_query(
            "再按渠道拆分一下", context("统计最近30天各地区的销售额")
        )
        self.assertTrue(changed)
        self.assertIn("最近30天", actual)
        self.assertIn("各地区", actual)
        self.assertIn("销售额", actual)
        self.assertIn("按渠道拆分", actual)

    def test_4_complete_new_question_does_not_inherit(self):
        actual, changed = rewrite_query(
            "统计最近7天的订单数量", context("查询华东地区最近30天的销售额")
        )
        self.assertFalse(changed)
        self.assertEqual(actual, "统计最近7天的订单数量")
        self.assertNotIn("华东", actual)
        self.assertNotIn("销售额", actual)

    def test_5_three_turn_chain(self):
        first = context("查询最近30天华东地区的销售额")
        second, changed = rewrite_query("那华南呢？", first)
        self.assertTrue(changed)
        third, changed = rewrite_query("再改成上个月", context(second))
        self.assertTrue(changed)
        self.assertIn("上个月", third)
        self.assertIn("华南", third)
        self.assertIn("销售额", third)
        self.assertNotIn("最近30天", third)

    def test_session_isolation(self):
        graph = FakeGraph()
        service = make_service(graph)
        asyncio.run(service.run_once("查询华东销售额", session_id="a"))
        asyncio.run(service.run_once("那华南呢？", session_id="b"))
        self.assertEqual(graph.inputs[1]["query"], "那华南呢？")

    def test_no_history_does_not_invent_context(self):
        actual, changed = rewrite_query("那华南呢？", None)
        self.assertFalse(changed)
        self.assertEqual(actual, "那华南呢？")

    def test_failed_turn_is_not_saved(self):
        graph = FakeGraph({"intent": "query_database", "agent_result": {"error": "db failed"}})
        service = make_service(graph)
        asyncio.run(service.run_once("查询华东销售额", session_id="failed"))
        self.assertIsNone(conversation_store.get("failed"))
        asyncio.run(service.run_once("那华南呢？", session_id="failed"))
        self.assertEqual(graph.inputs[1]["query"], "那华南呢？")

    def test_single_turn_and_other_agent_are_unchanged(self):
        graph = FakeGraph({"intent": "direct_answer", "agent_result": {"error": None}})
        service = make_service(graph)
        asyncio.run(service.run_once("帮我解释什么是RAG", session_id="knowledge"))
        self.assertEqual(graph.inputs[0]["query"], "帮我解释什么是RAG")
        self.assertIsNone(conversation_store.get("knowledge"))

    def test_api_session_and_sse_compatibility(self):
        request = QuerySchema(query="查询销售额", session_id="browser-session")
        self.assertEqual(request.session_id, "browser-session")
        graph = FakeGraph()
        events = asyncio.run(self._collect(make_service(graph)))
        self.assertEqual(events, [
            {"type": "session", "session_id": "browser-session"},
            {"type": "bi_report", "content": "ok"},
        ])

    async def _collect(self, service):
        return [event async for event in service.stream_chat("查询销售额", session_id="browser-session")]
