"""只读策略、执行入口及真实校验/纠错节点的离线回归测试。"""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, START, StateGraph

from app.agent.data_agent.context import DataAgentContext
from app.agent.data_agent.graph import route_after_validation as full_route
from app.agent.data_agent.query_graph import route_after_validation as query_route
from app.agent.data_agent.nodes.correct_sql import correct_sql
from app.agent.data_agent.nodes.execute_sql import execute_sql
from app.agent.data_agent.nodes.validate_sql import validate_sql
from app.agent.data_agent.state import DataAgentState
from app.core.sql_safety import SQLSafetyPolicy
from app.evaluation.runner import refresh_gold
from app.evaluation.schema import EvalCase, load_cases
from app.repositories.mysql.dw_mysql_repository import DWMySQLRepository
from app.service.sql_validation_service import SQLSafetyPolicy as ServicePolicy, SQLValidationService


SAFE_SQL = (
    "SELECT 1 AS value",
    "  select COUNT(*) FROM fact_order;  ",
    "WITH orders AS (SELECT 1 AS value) SELECT * FROM orders",
    "WITH RECURSIVE n AS (SELECT 1 AS x UNION ALL SELECT x+1 FROM n WHERE x<3) SELECT * FROM n",
    "SELECT /* ordinary comment */ 1",
)
UNSAFE_SQL = (
    "DELETE FROM fact_order",
    "UPDATE fact_order SET pay_amount=0",
    "DROP TABLE fact_order",
    "INSERT INTO fact_order VALUES (1)",
    "ALTER TABLE fact_order ADD x INT",
    "SELECT 1; DELETE FROM fact_order",
    "SELECT 1; SELECT 2",
    "SELECT 1;;",
    "WITH orders AS (SELECT 1) DELETE FROM fact_order",
    "WITH orders AS (SELECT 1) UPDATE fact_order SET pay_amount=0",
    "SELECT 1 INTO OUTFILE '/tmp/export'",
    "SELECT 1 INTO/**/DUMPFILE '/tmp/export'",
    "SELECT 1 INTO @value",
    "SELECT @value := 1",
    "SELECT 1 /*!50000 INTO OUTFILE '/tmp/export' */",
    "SELECT 1 /*M!100100 INTO OUTFILE '/tmp/export' */",
    "SELECT * FROM fact_order FOR UPDATE",
    "SELECT * FROM fact_order FOR SHARE",
    "SELECT * FROM fact_order FOR/**/SHARE",
    "SELECT * FROM fact_order LOCK IN SHARE MODE",
    "SELECT GET_LOCK/**/('key', 1)",
    "SELECT RELEASE_LOCK('key')",
    "SELECT RELEASE_ALL_LOCKS()",
    "SELECT SLEEP(1)",
    "SELECT BENCHMARK(100, 1+1)",
    "SELECT LOAD_FILE('/etc/passwd')",
    "SELECTED 1",
    "WITHHOLD 1",
    "SHOW TABLES",
    "",
    "   ; ",
)


def make_repository():
    return SimpleNamespace(
        validate_sql=AsyncMock(),
        get_used_columns_from_sql=AsyncMock(return_value=[]),
        get_all_table_names=AsyncMock(return_value=[]),
        execute_sql=AsyncMock(return_value=[{"value": 1}]),
    )


class SQLSafetyPolicyTest(unittest.TestCase):
    def test_shared_policy_preserves_old_import(self):
        self.assertIs(ServicePolicy, SQLSafetyPolicy)

    def test_select_and_cte_are_allowed(self):
        for sql in SAFE_SQL:
            with self.subTest(sql=sql):
                SQLSafetyPolicy.ensure_read_only_sql(sql)

    def test_writes_multi_statements_and_known_bypasses_are_rejected(self):
        for sql in UNSAFE_SQL:
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                SQLSafetyPolicy.ensure_read_only_sql(sql)

    def test_existing_evaluation_gold_queries_remain_accepted(self):
        for case in load_cases():
            if case.gold_sql:
                with self.subTest(case=case.id):
                    SQLSafetyPolicy.ensure_read_only_sql(case.gold_sql)


class ValidationNodeTest(unittest.IsolatedAsyncioTestCase):
    async def test_unsafe_sql_never_reaches_explain_or_schema(self):
        repository = make_repository()
        runtime = SimpleNamespace(context={"dw_mysql_repository": repository}, stream_writer=Mock())
        for sql in UNSAFE_SQL:
            with self.subTest(sql=sql):
                result = await validate_sql({"sql": sql, "retry_count": 1}, runtime)
                self.assertTrue(result["error"])
                self.assertEqual(result["retry_count"], 2)
        repository.validate_sql.assert_not_awaited()
        repository.get_used_columns_from_sql.assert_not_awaited()
        repository.get_all_table_names.assert_not_awaited()

    async def test_read_only_validation_precedes_existing_references(self):
        repository = make_repository()
        runtime = SimpleNamespace(context={"dw_mysql_repository": repository}, stream_writer=Mock())
        calls = []
        repository.validate_sql.side_effect = lambda sql: calls.append("explain")

        async def references(**kwargs):
            calls.append("references")
            self.assertEqual(kwargs["table_infos"], [{"name": "fact_order"}])
            return []

        with patch.object(SQLValidationService, "validate_references", side_effect=references):
            result = await validate_sql({"sql": SAFE_SQL[0], "table_infos": [{"name": "fact_order"}]}, runtime)
        self.assertEqual(calls, ["explain", "references"])
        self.assertEqual(result, {"error": None})

    async def test_schema_failure_retains_error_and_retry_contract(self):
        repository = make_repository()
        runtime = SimpleNamespace(context={"dw_mysql_repository": repository}, stream_writer=Mock())
        with patch.object(SQLValidationService, "validate_references", new=AsyncMock(return_value=["未知字段"])):
            result = await validate_sql({"sql": SAFE_SQL[0], "retry_count": 2}, runtime)
        self.assertEqual(result, {"error": "SQL引用校验失败：未知字段", "retry_count": 3})

    async def test_explain_failure_skips_schema_and_enters_correction(self):
        repository = make_repository()
        repository.validate_sql.side_effect = ValueError("Unknown column")
        runtime = SimpleNamespace(context={"dw_mysql_repository": repository}, stream_writer=Mock())
        result = await validate_sql({"sql": SAFE_SQL[0]}, runtime)
        self.assertEqual(result, {"error": "Unknown column", "retry_count": 1})
        repository.get_used_columns_from_sql.assert_not_awaited()
        self.assertEqual(full_route(result), "correct_sql")
        self.assertEqual(query_route(result), "correct_sql")


class RepositorySafetyTest(unittest.IsolatedAsyncioTestCase):
    async def test_direct_execution_cannot_bypass_policy(self):
        session = SimpleNamespace(execute=AsyncMock())
        repository = DWMySQLRepository(session)
        for sql in UNSAFE_SQL:
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                await repository.execute_sql(sql)
        session.execute.assert_not_awaited()

    async def test_select_and_cte_preserve_rows_and_execute_only_once(self):
        result = Mock()
        result.mappings.return_value.fetchall.return_value = [{"value": 1}]
        session = SimpleNamespace(execute=AsyncMock(return_value=result))
        for sql in SAFE_SQL:
            with self.subTest(sql=sql):
                session.execute.reset_mock()
                rows = await DWMySQLRepository(session).execute_sql(sql)
                self.assertEqual(rows, [{"value": 1}])
                session.execute.assert_awaited_once()
                self.assertEqual(str(session.execute.call_args.args[0]), sql)

    async def test_metadata_show_columns_remains_available(self):
        result = Mock()
        result.fetchall.return_value = [SimpleNamespace(Field="value", Type="int")]
        session = SimpleNamespace(execute=AsyncMock(return_value=result))
        self.assertEqual(await DWMySQLRepository(session).get_column_types("fact_order"), {"value": "int"})
        self.assertEqual(str(session.execute.call_args.args[0]), "show columns from fact_order")

    async def test_evaluation_refresh_uses_guarded_entry_and_preserves_snapshot(self):
        result = Mock()
        result.mappings.return_value.fetchall.return_value = [{"value": 1}]
        session = SimpleNamespace(execute=AsyncMock(return_value=result))
        cases = [EvalCase(id=str(i), question="test", category="test", gold_sql=sql) for i, sql in enumerate(SAFE_SQL)]
        repository = DWMySQLRepository(session)
        self.assertIs(await refresh_gold(cases, repository), cases)
        self.assertTrue(all(case.expected_result == [{"value": "1"}] for case in cases))
        session.execute.reset_mock()
        with self.assertRaises(ValueError):
            await refresh_gold([EvalCase(id="unsafe", question="test", category="test", gold_sql=UNSAFE_SQL[0])], repository)
        session.execute.assert_not_awaited()


class CorrectionLoopTest(unittest.IsolatedAsyncioTestCase):
    async def run_loop(self, route, corrected_sql):
        """仅裁剪召回/展示阶段，使用生产校验、纠错、执行节点和路由函数。"""
        repository = make_repository()
        prompts = []

        def model_response(prompt):
            prompts.append(prompt.to_string())
            return AIMessage(content=corrected_sql)

        builder = StateGraph(DataAgentState, context_schema=DataAgentContext)
        builder.add_node("generate_sql", lambda state: {"sql": "DELETE FROM fact_order"})
        builder.add_node("validate_sql", validate_sql)
        builder.add_node("correct_sql", correct_sql)
        builder.add_node("execute_sql", execute_sql)
        builder.add_node("translate_error", lambda state: {"error_response": state["error"]})
        builder.add_edge(START, "generate_sql")
        builder.add_edge("generate_sql", "validate_sql")
        builder.add_conditional_edges("validate_sql", route)
        builder.add_edge("correct_sql", "validate_sql")
        builder.add_edge("execute_sql", END)
        builder.add_edge("translate_error", END)
        with patch("app.agent.data_agent.nodes.correct_sql.get_context_llm", return_value=RunnableLambda(model_response)):
            final = await builder.compile().ainvoke(
                {"query": "查询数据", "table_infos": [], "metric_infos": [], "date_info": {}, "db_info": {}},
                context={"dw_mysql_repository": repository},
            )
        return final, repository, prompts

    async def test_rejected_sql_is_corrected_revalidated_and_executed(self):
        for route in (full_route, query_route):
            with self.subTest(route=route.__module__):
                final, repository, prompts = await self.run_loop(route, SAFE_SQL[0])
                self.assertIsNone(final["error"])
                self.assertEqual(final["retry_count"], 1)
                self.assertEqual(final["sql_result"], [{"value": 1}])
                repository.validate_sql.assert_awaited_once_with(SAFE_SQL[0])
                repository.get_used_columns_from_sql.assert_awaited_once_with(SAFE_SQL[0])
                repository.execute_sql.assert_awaited_once_with(SAFE_SQL[0])
                self.assertEqual(len(prompts), 1)
                self.assertIn("DELETE FROM fact_order", prompts[0])
                self.assertIn("禁止", prompts[0])

    async def test_three_failed_validations_stop_without_executing(self):
        for route in (full_route, query_route):
            with self.subTest(route=route.__module__):
                final, repository, prompts = await self.run_loop(route, "UPDATE fact_order SET pay_amount=0")
                self.assertEqual(final["retry_count"], 3)
                self.assertEqual(len(prompts), 2)
                self.assertEqual(final["error_response"], final["error"])
                repository.validate_sql.assert_not_awaited()
                repository.execute_sql.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
