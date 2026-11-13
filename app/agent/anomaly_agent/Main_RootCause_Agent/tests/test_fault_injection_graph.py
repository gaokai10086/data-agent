"""Full parent/child Graph routing with explicit, offline fault injection."""

import asyncio
import importlib
import json
import time
import unittest
from decimal import Decimal
from unittest.mock import patch

from langchain_core.runnables import RunnableLambda

from app.agent.anomaly_agent.DimensionAttribution_Agent.schemas import (
    DimensionMemberAttribution, ReconciliationResult,
)
from app.agent.anomaly_agent.DimensionAttribution_Agent.tests.test_skeleton import make_input
from app.agent.anomaly_agent.DimensionAttribution_Agent.tools import AttributionQueryError, ATTRIBUTION_TOOL_REGISTRY
from app.agent.anomaly_agent.Main_RootCause_Agent.actions.registry import ACTION_REGISTRY, ActionSpec
from app.agent.anomaly_agent.schemas import MetricDimension


root_graph_module = importlib.import_module("app.agent.anomaly_agent.Main_RootCause_Agent.graph")
child_nodes = importlib.import_module("app.agent.anomaly_agent.DimensionAttribution_Agent.nodes")


async def _extract(_state, runtime):
    return {"extracted_metrics": ["销售额"]}


async def _select_schema(_state, runtime):
    return {"schema_result": {
        "status": "matched", "task_type": "dimension_attribution",
        "schema_name": "DimensionAttributionRequest",
    }}


async def _parse(_state, runtime):
    return {"parsed_result": {"status": "passed", "parsed_request": {
        "metric": "销售额", "target_period": {"year": 2026, "month": 8},
        "comparison_period": {"year": 2026, "month": 7},
        "filters": [{"dimension": "城市", "value": "北京", "operator": "="}],
        "expected_direction": "decrease", "dimensions": [],
    }}}


class InjectedAttributionTool:
    name = "dimension_period_comparison"

    def __init__(self, fail_second):
        self.calls = 0
        self.fail_second = fail_second

    async def run(self, input_data, _runtime, **kwargs):
        self.calls += 1
        if self.calls == 2 and self.fail_second:
            raise AttributionQueryError("injected_sql_failure", 1)
        member = DimensionMemberAttribution(
            group_value="sample-member", comparison_value=Decimal("2"),
            current_value=Decimal("1"), change_amount=Decimal("-1"),
            change_rate=Decimal("-0.5"), net_contribution_rate=Decimal("1"),
        )
        reconciliation = ReconciliationResult(
            comparison_sum=Decimal("2"), current_sum=Decimal("1"),
            summed_change_amount=Decimal("-1"), overall_comparison_value=Decimal("2"),
            overall_current_value=Decimal("1"), overall_change_amount=Decimal("-1"),
            comparison_matches=True, current_matches=True, change_matches=True,
            tolerance=Decimal("0.01"),
        )
        dimension = next(item for item in input_data.metric_context.dimensions
                         if item.column == kwargs["dimension_column"])
        return {
            "dimension": dimension, "task_id": kwargs["task_id"],
            "depth": kwargs["depth"], "parent_scope": kwargs["parent_scope"],
            "members": [member], "reconciliation": reconciliation,
            "sql_by_period": {"2026-07": "injected-select-july", "2026-08": "injected-select-august"},
            "query_count": 2, "informative": True, "null_member_present": False,
        }


class FaultInjectionGraphTest(unittest.IsolatedAsyncioTestCase):
    async def _run(self, *, fail_second):
        fixture = make_input()
        fixture.metric_context.dimensions = [
            MetricDimension(name="城市", column="dim_store.city"),
            MetricDimension(name="渠道", column="dim_store.channel_type"),
            MetricDimension(name="门店", column="dim_store.store_name"),
        ]
        fixture.inspection_outcome.metric_context = fixture.metric_context
        tool = InjectedAttributionTool(fail_second)
        decision_count = 0

        async def fake_llm(_prompt):
            nonlocal decision_count
            decision_count += 1
            column = (
                "dim_store.channel_type" if decision_count == 1 else
                "dim_store.store_name" if fail_second else "dim_store.not_allowed"
            )
            return json.dumps({
                "action": "execute_tool", "tool_name": tool.name,
                "dimension_column": column, "parent_scope": [], "depth": 0,
                "reason": "injected_decision",
            })

        async def ready_action(_request, _runtime):
            return fixture.inspection_outcome

        change_spec = ActionSpec("change_inspection", "change_inspection", "period_comparison", ready_action)
        patches = (
            patch.object(root_graph_module, "extract_metrics", _extract),
            patch.object(root_graph_module, "select_task_schema", _select_schema),
            patch.object(root_graph_module, "parse_task_request", _parse),
            patch.dict(ACTION_REGISTRY, {"change_inspection": change_spec}),
            patch.dict(ATTRIBUTION_TOOL_REGISTRY, {tool.name: tool}),
            patch.object(child_nodes, "get_context_llm", return_value=RunnableLambda(fake_llm)),
        )
        for item in patches:
            item.start()
        try:
            graph = root_graph_module.build_root_cause_graph()
            nodes = []
            state = {}
            started = time.monotonic()
            async for mode, chunk in graph.astream(
                {"raw_question": "为什么 2026 年 8 月北京销售额比 7 月下降？"},
                context={"injected_test_context": True}, stream_mode=["updates", "values"],
            ):
                if mode == "updates":
                    nodes.extend(chunk)
                elif mode == "values":
                    state.update(chunk)
            pending = [task for task in asyncio.all_tasks()
                       if task is not asyncio.current_task() and not task.done()]
            return nodes, state, tool.calls, decision_count, len(pending), round((time.monotonic()-started)*1000, 1)
        finally:
            for item in reversed(patches):
                item.stop()

    async def test_partial_keeps_prior_observation(self):
        nodes, state, calls, decisions, pending, elapsed_ms = await self._run(fail_second=False)
        self.assertEqual(nodes.count("action_executor"), 2)
        self.assertEqual(calls, 1)
        self.assertEqual(decisions, 2)
        self.assertEqual(pending, 0)
        observations = state["observations"]
        self.assertEqual(observations[0]["status"], "ready")
        self.assertEqual(observations[1]["status"], "partial")
        result = observations[1]["attribution_result"]
        self.assertEqual((result["status"], result["completion_status"], result["stop_reason"]),
                         ("completed", "partial", "illegal_decision"))
        self.assertEqual(len(result["paths"]), 1)
        self.assertEqual(result["total_query_count"], 2)
        print("D", {"nodes": nodes, "child_nodes": observations[1]["evidence"]["nodes"],
                    "status": result["status"], "completion_status": result["completion_status"],
                    "stop_reason": result["stop_reason"], "queries": result["total_query_count"],
                    "rounds": len(result["decisions"]), "pending": pending, "elapsed_ms": elapsed_ms})

    async def test_failed_keeps_prior_observation(self):
        nodes, state, calls, decisions, pending, elapsed_ms = await self._run(fail_second=True)
        self.assertEqual(nodes.count("action_executor"), 2)
        self.assertEqual(calls, 2)
        self.assertEqual(decisions, 2)
        self.assertEqual(pending, 0)
        observations = state["observations"]
        self.assertEqual(observations[0]["status"], "ready")
        self.assertEqual(observations[1]["status"], "failed")
        result = observations[1]["attribution_result"]
        self.assertEqual((result["status"], result["completion_status"], result["stop_reason"]),
                         ("failed", "partial", "execution_failed"))
        self.assertEqual(len(result["paths"]), 1)
        self.assertEqual(result["total_query_count"], 3)
        self.assertEqual(state["supervisor_decision"]["action"], "stop")
        print("E", {"nodes": nodes, "child_nodes": observations[1]["evidence"]["nodes"],
                    "status": result["status"], "completion_status": result["completion_status"],
                    "stop_reason": result["stop_reason"], "queries": result["total_query_count"],
                    "rounds": len(result["decisions"]), "pending": pending, "elapsed_ms": elapsed_ms})
