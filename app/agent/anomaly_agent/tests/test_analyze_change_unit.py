"""离线回归：动作约束、工具结果计算、反馈循环、预算与图接线。"""
import asyncio
import importlib
import unittest
from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from pydantic import ValidationError

from app.agent.anomaly_agent.handlers.dimension_contribution import (
    MAX_GROUPS, calculate_contributions, parse_groups, query_dimension,
)
from app.agent.anomaly_agent.schemas import (
    AnalysisAction, AnalysisContext, AnalysisRequest, ChangeVerification,
    MetricContext, OverallComparisonResult,
)
from app.agent.anomaly_agent.skills.registry import build_analysis_strategy


node = importlib.import_module("app.agent.anomaly_agent.nodes.analyze_change")
tool = importlib.import_module("app.agent.anomaly_agent.handlers.dimension_contribution")


def make_context():
    request = AnalysisRequest(
        request_id="test-request", metric="销售额", target_period="2025-07",
        comparison_period="2025-06", comparison_type="mom", expected_direction="decrease",
        filters=[{"dimension": "城市", "value": "上海"}],
    )
    metric = MetricContext(
        metric_key="sales_amount", name="销售额", fact_table="fact_order",
        measure_column="pay_amount", aggregation="SUM", time_column="pay_time",
        filters=[{"column": "fact_order.order_status", "operator": "=", "value": "已完成"}],
        dimensions=[
            {"name": "城市", "column": "dim_store.city", "determines": ["dim_store.region"]},
            {"name": "大区", "column": "dim_store.region"},
            {"name": "支付时间", "column": "fact_order.pay_time"},
            {"name": "渠道", "column": "dim_store.channel_type"},
            {"name": "门店", "column": "dim_store.store_name"},
            {"name": "促销活动", "column": "dim_promotion.promo_name"},
            {"name": "用户", "column": "dim_user.user_id"},
        ],
    )
    comparison = OverallComparisonResult(
        request_id=request.request_id, metric_key=metric.metric_key,
        target_period=request.target_period, comparison_period=request.comparison_period,
        current_value=80, comparison_value=100, change_amount=-20, change_rate="-0.2",
    )
    return AnalysisContext(
        request=request, metric=metric, comparison=comparison,
        verification=ChangeVerification(decision="continue", reason="change_confirmed",
                                        expected_direction="decrease", actual_direction="decrease",
                                        summary="销售额从100降至80，下降20。"),
    )


def dimension(column="dim_store.channel_type"):
    return AnalysisAction(action="dimension_contribution", dimension_column=column,
                          reason="检查下降是否集中在少数分组")


def sample_rows():
    return ([{"group_value": "线下", "value": 40}, {"group_value": "线上", "value": 40}],
            [{"group_value": "线下", "value": 70}, {"group_value": "线上", "value": 30}])


class ContributionTest(unittest.TestCase):
    def test_offsets_and_signed_contribution_over_100_percent(self):
        groups = calculate_contributions(make_context(), *sample_rows())
        self.assertEqual(groups[0].change_amount, Decimal(-30))
        self.assertEqual(groups[0].contribution_rate, Decimal("1.5"))
        self.assertEqual(groups[1].contribution_rate, Decimal("-0.5"))
        self.assertEqual(sum(g.change_amount for g in groups), Decimal(-20))

    def test_new_missing_and_null_dimension_groups(self):
        groups = calculate_contributions(make_context(),
            [{"group_value": "新", "value": 30}, {"group_value": None, "value": 50}],
            [{"group_value": "旧", "value": 50}, {"group_value": None, "value": 50}])
        by_key = {g.group_value: g for g in groups}
        self.assertEqual(by_key["新"].comparison_value, 0)
        self.assertEqual(by_key["旧"].current_value, 0)
        self.assertEqual(by_key[None].change_amount, 0)

    def test_reject_each_period_mismatch_even_if_deltas_match(self):
        with self.assertRaisesRegex(ValueError, "总体值"):
            calculate_contributions(make_context(), [{"group_value": "a", "value": 81}],
                                    [{"group_value": "a", "value": 101}])

    def test_reject_invalid_or_truncated_results(self):
        bad = [[], None, [{"group_value": "a", "value": None}],
               [{"group_value": "a", "value": "NaN"}],
               [{"group_value": "a", "value": True}],
               [{"group_value": 12, "value": 1}],
               [{"group_value": "a", "value": 1}] * 2,
               [{"group_value": "a", "value": 1, "extra": 2}],
               [{"group_value": str(i), "value": 1} for i in range(MAX_GROUPS + 1)]]
        for rows in bad:
            with self.subTest(rows=str(rows)[:80]), self.assertRaises(ValueError):
                parse_groups(rows)

    def test_action_schema_rejects_scope_sql_and_unsupported_methods(self):
        for payload in [
            dict(action="metric_decomposition", reason="拆指标"),
            dict(action="dimension_contribution", reason="缺少维度"),
            dict(action="finish", dimension_column="a.b", reason="结束"),
            dict(action="finish", reason="结束", sql="SELECT 1"),
            dict(action="finish", reason="结束", filters=[]),
        ]:
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                AnalysisAction.model_validate(payload)


class AnalysisLoopTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # 预期失败路径仍测试报告和控制流，避免将异常堆栈刷满测试终端。
        log_patch = patch.object(node.logger, "exception")
        log_patch.start()
        self.addCleanup(log_patch.stop)
        self.context = make_context()
        self.state = {
            "raw_question": "为什么上海销售额下降？", "analysis_request": self.context.request,
            "metric_context": self.context.metric, "analysis_result": self.context.comparison,
            "change_verification": self.context.verification,
            "analysis_strategy": await build_analysis_strategy(self.context),
        }
        self.events = []
        self.runtime = SimpleNamespace(context={}, stream_writer=self.events.append)
        self.groups = calculate_contributions(self.context, *sample_rows())

    async def run_loop(self, actions, executor=None):
        decisions = AsyncMock(side_effect=actions)
        executor = executor or AsyncMock(return_value=(self.groups, {"2025-07": "SELECT ..."}))
        with patch.object(node, "decide_action", decisions), patch.object(node, "query_dimension", executor):
            result = await node.analyze_change(self.state, self.runtime)
        return result["change_analysis_report"], decisions, executor

    async def test_result_is_fed_back_before_next_decision(self):
        seen = []

        async def choose(context, candidates, observations, rounds_left, runtime):
            root_candidates = [a.dimension_column for a in node.available_actions(context, candidates, observations)
                               if a.scope_ref is None]
            seen.append(([o.status for o in observations], root_candidates))
            return dimension() if not observations else AnalysisAction(action="finish", reason="已有证据")

        with patch.object(node, "decide_action", side_effect=choose), patch.object(
            node, "query_dimension", AsyncMock(return_value=(self.groups, {}))):
            report = (await node.analyze_change(self.state, self.runtime))["change_analysis_report"]
        self.assertEqual(seen[0][0], [])
        self.assertEqual(seen[1][0], ["ok"])
        self.assertNotIn("dim_store.channel_type", seen[1][1])
        for fixed in ("dim_store.city", "dim_store.region", "fact_order.pay_time", "dim_user.user_id"):
            self.assertNotIn(fixed, seen[0][1])
        self.assertEqual(report.stop_reason, "model_finished")
        self.assertIn("dimension_1", report.summary)
        self.assertIn("不证明业务因果", report.summary)
        self.assertEqual([e["type"] for e in self.events],
                         ["analysis_action", "analysis_observation", "analysis_action"])

    async def test_finish_without_query(self):
        report, _, executor = await self.run_loop([AnalysisAction(action="finish", reason="只展示总体")])
        executor.assert_not_awaited()
        self.assertEqual(report.stop_reason, "model_finished")
        self.assertNotIn("对账通过", report.summary)

    async def test_illegal_fixed_or_repeated_dimension_never_executes(self):
        for column in ("dim_store.city", "invented.column"):
            report, _, executor = await self.run_loop([dimension(column)])
            self.assertEqual(report.stop_reason, "invalid_action")
            executor.assert_not_awaited()
        report, _, executor = await self.run_loop([dimension(), dimension()])
        self.assertEqual(report.stop_reason, "invalid_action")
        self.assertEqual(executor.await_count, 1)

    async def test_tool_error_is_observation_and_not_a_conclusion(self):
        executor = AsyncMock(side_effect=ValueError("secret database error"))
        report, _, executor = await self.run_loop(
            [dimension(), AnalysisAction(action="finish", reason="工具不可用")], executor)
        self.assertEqual(report.observations[0].status, "error")
        self.assertEqual(report.observations[0].groups, [])
        self.assertNotIn("secret", report.model_dump_json())
        self.assertNotIn("对账通过", report.summary)

    async def test_failed_dimension_cannot_be_retried(self):
        report, _, executor = await self.run_loop([dimension(), dimension()], AsyncMock(side_effect=ValueError()))
        self.assertEqual(report.stop_reason, "invalid_action")
        self.assertEqual(executor.await_count, 1)

    async def test_budget_is_enforced_in_code(self):
        with patch.object(node, "MAX_ROUNDS", 1):
            report, decisions, executor = await self.run_loop([dimension()])
        self.assertEqual(report.stop_reason, "budget_exhausted")
        self.assertEqual(decisions.await_count, 1)
        self.assertEqual(executor.await_count, 1)

    async def test_decision_error_returns_partial_report(self):
        report, _, executor = await self.run_loop([dimension(), ValueError("bad JSON")])
        self.assertEqual(report.stop_reason, "decision_error")
        self.assertEqual(len(report.observations), 1)
        self.assertEqual(executor.await_count, 1)

    async def test_no_candidates_does_not_call_model(self):
        self.state["analysis_strategy"] = None
        report, decisions, executor = await self.run_loop([])
        self.assertEqual(report.stop_reason, "no_candidates")
        decisions.assert_not_awaited()
        executor.assert_not_awaited()

    async def test_verification_stop_does_not_call_model(self):
        self.state["change_verification"] = self.context.verification.model_copy(update={"decision": "stop"})
        report, decisions, _ = await self.run_loop([])
        self.assertEqual(report.stop_reason, "verification_stopped")
        decisions.assert_not_awaited()

    async def test_decision_timeout(self):
        async def slow(*args):
            await asyncio.sleep(1)
        with patch.object(node, "DECISION_TIMEOUT_SECONDS", 0.01), patch.object(node, "decide_action", slow):
            report = (await node.analyze_change(self.state, self.runtime))["change_analysis_report"]
        self.assertEqual(report.stop_reason, "timeout")

    async def test_tool_timeout_is_failed_observation(self):
        async def slow(*args):
            await asyncio.sleep(1)
        with patch.object(node, "TOOL_TIMEOUT_SECONDS", 0.01):
            report, _, _ = await self.run_loop(
                [dimension(), AnalysisAction(action="finish", reason="停止")], AsyncMock(side_effect=slow))
        self.assertEqual(report.observations[0].status, "error")
        self.assertIn("超时", report.observations[0].summary)

    async def test_total_timeout_preserves_prior_success(self):
        count = 0
        async def choose(*args):
            nonlocal count
            count += 1
            if count == 1:
                return dimension()
            await asyncio.sleep(1)
        with patch.object(node, "TOTAL_TIMEOUT_SECONDS", 0.03), patch.object(
            node, "decide_action", side_effect=choose), patch.object(
            node, "query_dimension", AsyncMock(return_value=(self.groups, {}))):
            report = (await node.analyze_change(self.state, self.runtime))["change_analysis_report"]
        self.assertEqual(report.stop_reason, "timeout")
        self.assertEqual(len(report.observations), 1)
        self.assertEqual(report.observations[0].status, "ok")

    async def test_cancellation_is_not_swallowed(self):
        with patch.object(node, "decide_action", AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await node.analyze_change(self.state, self.runtime)

    async def test_real_prompt_chain_with_fake_llm(self):
        prompts = []
        def respond(prompt):
            prompts.append(prompt.to_string())
            return AIMessage(content='{"action":"finish","reason":"暂时结束"}')
        with patch.object(node, "get_context_llm", return_value=RunnableLambda(respond)):
            action = await node.decide_action(self.context, ["dim_store.channel_type"], [], 3, self.runtime)
        self.assertEqual(action.action, "finish")
        self.assertIn("上海", prompts[0])
        self.assertIn("available_actions", prompts[0])

    async def test_query_tool_preserves_scope_and_sessions(self):
        sessions, queries = [], []
        @asynccontextmanager
        async def factory():
            session = object()
            sessions.append(session)
            yield session
        current, comparison = sample_rows()
        async def fake_stream(**kwargs):
            queries.append(kwargs["input"]["query"])
            self.assertIs(kwargs["context"]["dw_mysql_repository"].dw_session, sessions[-1])
            yield "custom", {"stage": "执行SQL"}
            yield "values", {"sql": "SELECT group_value, value FROM example",
                             "sql_result": current if len(queries) == 1 else comparison}
        self.runtime.context["dw_session_factory"] = factory
        with patch.object(tool.data_query_graph, "astream", side_effect=fake_stream):
            groups, sqls = await query_dimension(self.context, "dim_store.channel_type", self.runtime)
        self.assertEqual(len(sessions), 2)
        self.assertIsNot(sessions[0], sessions[1])
        self.assertEqual(len(sqls), 2)
        self.assertEqual(groups[0].change_amount, -30)
        for query in queries:
            for text in ("上海", "已完成", "group_value", "dim_store.channel_type", "101"):
                self.assertIn(text, query)

    async def test_graph_has_single_new_analysis_node_and_correct_edges(self):
        from app.agent.anomaly_agent.graph import anomaly_graph
        graph = anomaly_graph.get_graph()
        edges = {(e.source, e.target) for e in graph.edges}
        self.assertIn(("plan_analysis", "analyze_change"), edges)
        self.assertIn(("analyze_change", "__end__"), edges)
        self.assertNotIn(("plan_analysis", "__end__"), edges)

    async def test_compiled_node_writes_report_to_state(self):
        from langgraph.graph import END, START, StateGraph
        from app.agent.anomaly_agent.state import AnomalyState
        builder = StateGraph(AnomalyState)
        builder.add_node("analyze_change", node.analyze_change)
        builder.add_edge(START, "analyze_change")
        builder.add_edge("analyze_change", END)
        before = self.context.model_dump()
        with patch.object(node, "decide_action", AsyncMock(side_effect=[
            dimension(), AnalysisAction(action="finish", reason="已有证据")
        ])), patch.object(node, "query_dimension", AsyncMock(return_value=(self.groups, {}))):
            result = await builder.compile().ainvoke(self.state)
        self.assertEqual(result["change_analysis_report"].stop_reason, "model_finished")
        self.assertEqual(self.context.model_dump(), before)

    async def test_adapter_outputs_report_summary_instead_of_stale_plan_text(self):
        from app.agent.orchestrator.nodes.run_anomaly_agent import run_anomaly_agent
        from app.agent.anomaly_agent.schemas import ChangeAnalysisReport
        report = ChangeAnalysisReport(request_id="test-request", stop_reason="model_finished",
                                      summary="[dimension_1] 已完成阶段性分析，不证明因果。")
        state = {**self.state, "change_analysis_report": report}
        with patch("app.agent.orchestrator.nodes.run_anomaly_agent.anomaly_graph.ainvoke",
                   AsyncMock(return_value=state)):
            output = await run_anomaly_agent({"query": "上海销售额为什么下降？"}, self.runtime)
        self.assertIs(output["agent_result"]["change_analysis_report"], report)
        self.assertEqual(self.events[-1]["type"], "token")
        self.assertEqual(self.events[-1]["content"], report.summary)

    async def test_tool_rejects_error_or_missing_sql_before_contribution(self):
        @asynccontextmanager
        async def factory():
            yield object()
        self.runtime.context["dw_session_factory"] = factory
        for final in ({"error_response": "failed"}, {"sql_result": sample_rows()[0]}):
            async def fake_stream(**kwargs):
                yield "values", final
            with self.subTest(final=final), patch.object(tool.data_query_graph, "astream", side_effect=fake_stream):
                with self.assertRaises((ValueError, RuntimeError)):
                    await query_dimension(self.context, "dim_store.channel_type", self.runtime)


if __name__ == "__main__":
    unittest.main()
