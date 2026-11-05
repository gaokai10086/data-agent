"""离线故障注入：驱动分类、事务恢复、图路由和 SSE 收尾。"""
import asyncio
import json
import importlib
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from asyncmy.errors import OperationalError, ProgrammingError
from sqlalchemy.exc import DBAPIError
from langgraph.graph import START, END, StateGraph

from app.agent.data_agent.context import DataAgentContext
from app.agent.data_agent.state import DataAgentState
from app.agent.data_agent.graph import graph
from app.agent.data_agent.query_graph import data_query_graph
from app.agent.data_agent.sql_routing import route_after_validation, route_after_execution
from app.agent.data_agent.nodes.validate_sql import validate_sql
from app.agent.data_agent.nodes.execute_sql import execute_sql
from app.agent.data_agent.nodes.translate_error import translate_error
from app.service.sql_error_handling import classify_sql_error, run_sql_operation, safe_error_detail
from app.tests.test_sql_read_only_safety import make_repository


def db_error(code, message="injected failure", invalidated=False):
    return DBAPIError("SELECT secret", {"password": "secret"}, OperationalError(code, message), connection_invalidated=invalidated)


class ClassificationTest(unittest.TestCase):
    def test_driver_codes_and_sqlalchemy_wrappers(self):
        for kind, codes in {
            "repairable": (1052, 1054, 1064, 1109, 1146, 1149),
            "permission": (1044, 1045, 1142, 1143, 1227, 1370, 1698),
            "connection": (2002, 2003, 2006, 2013, 2055),
            "unknown": (9999, 1213, 1205, 1040, 1049),
        }.items():
            for code in codes:
                with self.subTest(code=code):
                    self.assertEqual(classify_sql_error(db_error(code)), kind)
                    self.assertEqual(classify_sql_error(ProgrammingError(code, "failure")), kind)
        self.assertEqual(classify_sql_error(db_error(9999, invalidated=True)), "connection")
        self.assertEqual(classify_sql_error(ValueError("SQL permission timeout")), "unknown")
        self.assertEqual(classify_sql_error(TimeoutError()), "timeout")

    def test_log_redaction_omits_sqlalchemy_parameters(self):
        detail = safe_error_detail(db_error(2003, "mysql+asyncmy://admin:secret@host/db password=topsecret"))
        self.assertNotIn("secret", detail)
        self.assertNotIn("SELECT", detail)
        self.assertIn("2003", detail)

    def test_production_graphs_use_conditional_execution_edges(self):
        for compiled in (graph, data_query_graph):
            edges = [edge for edge in compiled.get_graph().edges if edge.source == "execute_sql"]
            self.assertTrue(all(edge.conditional for edge in edges))
            self.assertTrue({"correct_sql", "translate_error"}.issubset({e.target for e in edges}))


class RetryTest(unittest.IsolatedAsyncioTestCase):
    async def test_connection_recovery_rolls_back_before_retry(self):
        events = []
        async def rollback():
            events.append("rollback")
        async def operation():
            events.append("execute")
            if events.count("execute") < 3:
                raise db_error(2013)
            return [{"value": 1}]
        repository = SimpleNamespace(dw_session=SimpleNamespace(rollback=AsyncMock(side_effect=rollback)))
        with patch("app.service.sql_error_handling.asyncio.sleep", new=AsyncMock()) as sleep:
            self.assertEqual(await run_sql_operation(repository, operation), [{"value": 1}])
        self.assertEqual(events, ["execute", "rollback", "execute", "rollback", "execute"])
        self.assertEqual([c.args[0] for c in sleep.await_args_list], [0.25, 0.5])

    async def test_retry_exhaustion_and_non_transient_failures(self):
        for code, attempts in ((2003, 3), (1045, 1), (1064, 1), (9999, 1), (1205, 1)):
            operation = AsyncMock(side_effect=db_error(code))
            session = SimpleNamespace(rollback=AsyncMock())
            with self.subTest(code=code), patch("app.service.sql_error_handling.asyncio.sleep", new=AsyncMock()):
                with self.assertRaises(DBAPIError):
                    await run_sql_operation(SimpleNamespace(dw_session=session), operation)
                self.assertEqual(operation.await_count, attempts)
                self.assertEqual(session.rollback.await_count, attempts)

    async def test_timeout_cancels_operation_and_does_not_replay(self):
        cancelled = []
        async def slow():
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)
        operation = AsyncMock(side_effect=slow)
        session = SimpleNamespace(rollback=AsyncMock())
        with patch("app.service.sql_error_handling.DB_TIMEOUT_SECONDS", 0.01):
            with self.assertRaises(TimeoutError):
                await run_sql_operation(SimpleNamespace(dw_session=session), operation)
        self.assertEqual(cancelled, [True])
        operation.assert_awaited_once()
        session.rollback.assert_awaited_once()

    async def test_failed_cleanup_stops_retry_with_original_error(self):
        exc = db_error(2013)
        operation = AsyncMock(side_effect=exc)
        repository = SimpleNamespace(dw_session=SimpleNamespace(rollback=AsyncMock(side_effect=RuntimeError("cleanup"))))
        with self.assertRaises(DBAPIError) as caught:
            await run_sql_operation(repository, operation)
        self.assertIs(caught.exception, exc)
        operation.assert_awaited_once()

    async def test_request_cancellation_is_not_swallowed(self):
        operation = AsyncMock(side_effect=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await run_sql_operation(SimpleNamespace(), operation)
        operation.assert_awaited_once()


class RecoveryGraphTest(unittest.IsolatedAsyncioTestCase):
    async def run_graph(self, error, phase="execute", corrected_error=None):
        repository = make_repository()
        target = repository.execute_sql if phase == "execute" else repository.validate_sql
        target.side_effect = error
        correction = Mock(return_value={"sql": "SELECT 2 AS value"})
        if corrected_error is not None:
            target.side_effect = [error, corrected_error]
        builder = StateGraph(DataAgentState, context_schema=DataAgentContext)
        for name, node in (("validate_sql", validate_sql), ("execute_sql", execute_sql),
                           ("correct_sql", correction), ("translate_error", translate_error)):
            builder.add_node(name, node)
        builder.add_edge(START, "validate_sql")
        builder.add_conditional_edges("validate_sql", route_after_validation)
        builder.add_edge("correct_sql", "validate_sql")
        builder.add_conditional_edges("execute_sql", route_after_execution,
                                      {"result_analysis": END, "correct_sql": "correct_sql", "translate_error": "translate_error"})
        builder.add_edge("translate_error", END)
        events = []
        final = None
        with patch("app.service.sql_error_handling.asyncio.sleep", new=AsyncMock()):
            async for mode, chunk in builder.compile().astream(
                {"sql": "SELECT 1 AS value", "query": "test"},
                context={"dw_mysql_repository": repository}, stream_mode=["custom", "values"],
            ):
                if mode == "custom":
                    events.append(chunk)
                else:
                    final = chunk
        return final, repository, correction, events

    async def test_terminal_failures_never_correct_and_emit_one_safe_error(self):
        for phase in ("execute", "validate"):
            for code, attempts in ((1045, 1), (2003, 3), (9999, 1)):
                with self.subTest(phase=phase, code=code):
                    final, repo, correction, events = await self.run_graph(db_error(code, "password=secret"), phase)
                    correction.assert_not_called()
                    self.assertEqual(final["retry_count"], 0)
                    self.assertIsNone(final["sql_result"])
                    errors = [e for e in events if e.get("type") == "error_response"]
                    self.assertEqual(len(errors), 1)
                    self.assertNotIn("secret", json.dumps(events))
                    self.assertNotIn("password", errors[0]["content"])
                    target = repo.execute_sql if phase == "execute" else repo.validate_sql
                    self.assertEqual(target.await_count, attempts)
                    if phase == "validate":
                        repo.execute_sql.assert_not_awaited()

    async def test_execution_error_reenters_correction_and_validation(self):
        final, repo, correction, events = await self.run_graph(db_error(1054), corrected_error=None)
        # 持续的可修复错误沿用原来的三次失败上限。
        self.assertEqual(final["retry_count"], 3)
        self.assertEqual(correction.call_count, 2)
        self.assertEqual(repo.validate_sql.await_count, 3)
        self.assertEqual(repo.execute_sql.await_count, 3)

    async def test_execution_repair_succeeds_and_clears_error(self):
        final, repo, correction, events = await self.run_graph(db_error(1146), corrected_error=[{"value": 1}])
        self.assertIsNone(final["error"])
        self.assertIsNone(final["sql_error_kind"])
        self.assertEqual(final["sql_result"], [{"value": 1}])
        self.assertEqual(final["retry_count"], 1)
        self.assertEqual(repo.validate_sql.await_count, 2)
        correction.assert_called_once()
        self.assertFalse(any(e.get("type") == "error_response" for e in events))

    async def test_execution_safety_failure_stops(self):
        from app.core.sql_safety import SQLSafetyError
        final, repo, correction, events = await self.run_graph(SQLSafetyError("unsafe"))
        self.assertEqual(final["sql_error_kind"], "safety")
        self.assertIn("安全", final["error_response"])
        correction.assert_not_called()


class SSETest(unittest.IsolatedAsyncioTestCase):
    async def collect(self, service):
        # 本测试只验证 SSE 协议，以替身隔离同步 MySQL 会话库及其可选驱动。
        store = Mock()
        with patch.dict(sys.modules, {
            "app.service.mysql_conversation_repository": SimpleNamespace(conversation_repository=store)
        }):
            router = importlib.import_module("app.api.routers.chat_router")
        from app.schemas.chat import QuerySchema
        store.add_message.return_value = {"id": 1}
        with patch.object(router, "conversation_repository", store):
            response = await router.date_query(QuerySchema(query="test"), chat_service=service)
            return [json.loads(chunk.removeprefix("data: ").strip()) async for chunk in response.body_iterator]

    async def test_unhandled_error_is_safe_and_stream_ends(self):
        async def stream(*args, **kwargs):
            yield {"stage": "执行SQL语句"}
            raise db_error(2003, "mysql://admin:secret@host/db password=secret")
        events = await self.collect(SimpleNamespace(stream_chat=stream))
        self.assertEqual(events[-1]["type"], "error_response")
        self.assertNotIn("secret", json.dumps(events))
        self.assertNotIn("admin", json.dumps(events))

    async def test_success_report_and_classified_error_pass_through(self):
        for payload in (
            {"type": "bi_report", "data": [{"value": 1}], "chart_config": {"chart_type": "bar"},
             "analysis": {"row_count": 1}},
            {"type": "error_response", "content": "当前账号没有访问所需数据的权限。", "retry_count": 0},
        ):
            async def stream(*args, **kwargs):
                yield payload
            self.assertEqual(await self.collect(SimpleNamespace(stream_chat=stream)), [payload])


if __name__ == "__main__":
    unittest.main()
