"""Chat API 对异动 Agent 最终执行状态的识别测试。"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.api.routers import chat_router
from app.schemas.chat import QuerySchema


class ChatAnomalyStatusTest(unittest.IsolatedAsyncioTestCase):
    async def collect(self, payloads, *, exception: Exception | None = None):
        async def stream_chat(*args, **kwargs):
            for payload in payloads:
                yield payload
            if exception is not None:
                raise exception

        store = Mock()
        store.create_conversation.return_value = {"id": "conversation"}
        store.add_message.side_effect = [{"id": 1}, {"id": 2}]
        service = SimpleNamespace(stream_chat=stream_chat)
        query = QuerySchema(
            query="为什么销售额下降？",
            session_id="session",
            request_id="request",
        )

        with patch.object(chat_router, "conversation_repository", store):
            response = await chat_router.date_query(query, chat_service=service)
            raw_sse = [
                chunk.removeprefix("data: ").strip()
                async for chunk in response.body_iterator
            ]

        events = [json.loads(chunk) for chunk in raw_sse]
        assistant_calls = [
            call for call in store.add_message.call_args_list
            if call.kwargs.get("role") == "assistant"
        ]
        user_updates = [
            call for call in store.update_message.call_args_list
            if call.args and call.args[0] == 1
        ]
        return events, assistant_calls, user_updates

    async def test_ready_is_completed(self):
        payloads = [
            {
                "type": "anomaly_preparation_result",
                "agent": "anomaly_preparation",
                "status": "ready",
                "user_message": "可以继续分析",
            },
            {"type": "token", "content": "变化核验通过"},
        ]
        events, assistants, user_updates = await self.collect(payloads)

        self.assertEqual(events, payloads)
        self.assertEqual(assistants[-1].kwargs["status"], "completed")
        self.assertEqual(user_updates[-1].kwargs["status"], "completed")

    async def test_rejected_is_completed_business_stop(self):
        payloads = [
            {
                "type": "anomaly_preparation_result",
                "agent": "anomaly_preparation",
                "status": "rejected",
                "user_message": "未发生变化",
            },
            {
                "type": "change_verification",
                "agent": "anomaly",
                "decision": "stop",
                "summary": "未发生变化",
            },
        ]
        events, assistants, user_updates = await self.collect(payloads)

        self.assertEqual(events, payloads)
        self.assertEqual(assistants[-1].kwargs["status"], "completed")
        self.assertEqual(user_updates[-1].kwargs["status"], "completed")

    async def test_failed_preparation_event_is_failed(self):
        payloads = [
            {
                "type": "anomaly_preparation_result",
                "agent": "anomaly_preparation",
                "status": "failed",
                "user_message": "异动分析暂时无法完成。",
            },
            {"type": "token", "content": "后续普通事件"},
        ]
        events, assistants, user_updates = await self.collect(payloads)

        self.assertEqual(events, payloads)
        self.assertEqual(assistants[-1].kwargs["status"], "failed")
        self.assertEqual(user_updates[-1].kwargs["status"], "failed")

    async def test_current_error_response_protocol_is_failed_and_not_overwritten(self):
        payloads = [
            {
                "type": "error_response",
                "agent": "anomaly",
                "content": "异动分析暂时无法完成，请稍后重试。",
                "error": "异动分析暂时无法完成，请稍后重试。",
            },
            {
                "type": "anomaly_preparation_result",
                "agent": "anomaly_preparation",
                "status": "ready",
            },
            {"type": "token", "content": "后续普通事件"},
        ]
        events, assistants, user_updates = await self.collect(payloads)

        self.assertEqual(events, payloads)
        self.assertEqual(assistants[-1].kwargs["status"], "failed")
        self.assertEqual(user_updates[-1].kwargs["status"], "failed")

    async def test_unhandled_exception_is_saved_as_failed_and_sanitized(self):
        payloads = [{"type": "token", "content": "处理中"}]
        internal_error = "mysql://admin:secret@host/db password=secret"
        events, assistants, user_updates = await self.collect(
            payloads,
            exception=RuntimeError(internal_error),
        )

        self.assertEqual(events[0], payloads[0])
        self.assertEqual(events[-1]["type"], "error_response")
        self.assertNotIn("secret", json.dumps(events, ensure_ascii=False))
        self.assertNotIn("admin", json.dumps(events, ensure_ascii=False))
        self.assertEqual(assistants[-1].kwargs["status"], "failed")
        self.assertEqual(user_updates[-1].kwargs["status"], "failed")

    async def test_stream_error_after_error_response_does_not_send_duplicate(self):
        payloads = [
            {
                "type": "error_response",
                "agent": "anomaly",
                "content": "异动分析暂时无法完成，请稍后重试。",
            }
        ]
        events, assistants, _ = await self.collect(
            payloads,
            exception=RuntimeError("internal failure"),
        )

        errors = [event for event in events if event.get("type") == "error_response"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["agent"], "anomaly")
        self.assertEqual(assistants[-1].kwargs["status"], "failed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
