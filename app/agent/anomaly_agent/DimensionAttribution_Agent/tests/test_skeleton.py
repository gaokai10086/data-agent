"""第一阶段 State/Graph/契约测试。"""
import asyncio
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock
from langchain_core.runnables import RunnableLambda

from pydantic import ValidationError

from app.agent.anomaly_agent.DimensionAttribution_Agent.graph import dimension_attribution_graph
from app.agent.anomaly_agent.DimensionAttribution_Agent.nodes import execute_attribution_tools, main_reason
from app.agent.anomaly_agent.DimensionAttribution_Agent.nodes import _candidate_dimensions
from app.agent.anomaly_agent.DimensionAttribution_Agent.schemas import (
    AttributionDecision, AttributionObservation, DimensionAttributionInput, DimensionScope,
)
from app.agent.anomaly_agent.DimensionAttribution_Agent.state import DimensionAttributionState
from app.agent.anomaly_agent.DimensionAttribution_Agent.tools import ATTRIBUTION_TOOL_REGISTRY
from app.agent.anomaly_agent.Main_RootCause_Agent.skills.SalesAmountAnomalySkill.sales_amount_anomaly import SALES_AMOUNT_ANOMALY_SKILL
from app.agent.anomaly_agent.schemas import (
    AnalysisRequest,
    ChangeVerification,
    FilterCondition,
    InspectionOutcome,
    MetricContext,
    MetricDimension,
    OverallComparisonResult,
)


def make_input(*, ready=True, tools=()):
    request = AnalysisRequest(
        metric="销售额", request_id="req-1", target_period="2026-08",
        comparison_period="2026-07", comparison_type="mom",
    )
    metric = MetricContext(
        metric_key="sales_amount", name="销售额", fact_table="fact_order",
        measure_column="pay_amount", aggregation="SUM", time_column="pay_time",
        dimensions=[MetricDimension(name="城市", column="dim_store.city")],
    )
    overall = OverallComparisonResult(
        request_id="req-1", metric_key="sales_amount", target_period="2026-08",
        comparison_period="2026-07", current_value=Decimal("1"), comparison_value=Decimal("2"),
        change_amount=Decimal("-1"), change_rate=Decimal("-0.5"),
    )
    inspection = InspectionOutcome(
        status="ready" if ready else "rejected", reason="change_confirmed" if ready else "direction_mismatch",
        request_id="req-1", user_message="ok", metric_context=metric if ready else None,
        overall_result=overall if ready else None,
        verification=ChangeVerification(
            decision="continue" if ready else "stop", reason="change_confirmed" if ready else "direction_mismatch",
            expected_direction="decrease", actual_direction="decrease", summary="ok",
        ),
    )
    return DimensionAttributionInput(
        raw_question="分析销售额变化", request=request, inspection_outcome=inspection,
        metric_context=metric, skill_name=SALES_AMOUNT_ANOMALY_SKILL.name,
        skill_version=SALES_AMOUNT_ANOMALY_SKILL.version,
        skill_strategy=SALES_AMOUNT_ANOMALY_SKILL.strategy,
        available_tool_names=tools,
    )


class DimensionAttributionSkeletonTest(unittest.TestCase):
    def test_unready_input_is_rejected(self):
        with self.assertRaises(ValidationError):
            make_input(ready=False)

    def test_graph_returns_structured_unavailable_without_tools(self):
        result = asyncio.run(dimension_attribution_graph.ainvoke({"input": make_input()}))
        output = result["result"]
        self.assertEqual(output.status, "unavailable")
        self.assertEqual(output.reason, "no_tools_available")
        self.assertEqual(output.observations, [])
        self.assertEqual(output.total_query_count, 0)
        self.assertEqual(result["round_count"], 1)

    def test_state_isolated_between_invocations(self):
        first = asyncio.run(dimension_attribution_graph.ainvoke({"input": make_input()}))
        second_input = make_input()
        second_input.request.request_id = "req-2"
        second = asyncio.run(dimension_attribution_graph.ainvoke({"input": second_input}))
        self.assertEqual(first["result"].request_id, "req-1")
        self.assertEqual(second["result"].request_id, "req-2")
        self.assertEqual(first["observations"], [])
        self.assertEqual(second["observations"], [])

    def test_budget_termination_decision(self):
        input_data = make_input(tools=("future_tool",))
        state: DimensionAttributionState = {
            "input": input_data,
            "round_count": input_data.skill_strategy.max_analysis_rounds,
            "observations": [],
            "decisions": [],
        }
        update = asyncio.run(main_reason(state))
        self.assertEqual(update["stop_reason"], "budget_exhausted")
        self.assertEqual(update["decision"].action, "finish")

    def test_selected_unimplemented_tool_returns_unavailable_observation(self):
        input_data = make_input()
        state: DimensionAttributionState = {
            "input": input_data,
            "request_id": input_data.request.request_id,
            "decision": AttributionDecision(action="execute_tool", tool_name="future_tool", reason="test"),
            "observations": [],
        }
        update = asyncio.run(execute_attribution_tools(state))
        self.assertEqual(update["observations"][0].status, "unavailable")
        self.assertEqual(update["stop_reason"], "no_tools_available")

    def test_duplicate_dimension_is_stopped_without_query(self):
        input_data = make_input(tools=("dimension_period_comparison",))
        input_data.raw_question = "分析城市销售额变化"
        state: DimensionAttributionState = {
            "input": input_data,
            "round_count": 0,
            "query_count": 0,
            "analyzed_dimensions": ["dim_store.city"],
            "observations": [],
            "decisions": [],
        }
        update = asyncio.run(main_reason(state))
        self.assertEqual(update["stop_reason"], "no_candidate_dimensions")
        self.assertEqual(update["decision"].action, "finish")

    def test_query_budget_is_enforced_before_tool_selection(self):
        input_data = make_input(tools=("dimension_period_comparison",))
        state: DimensionAttributionState = {
            "input": input_data,
            "round_count": 0,
            "query_count": input_data.skill_strategy.max_query_budget,
            "analyzed_dimensions": [],
            "observations": [],
            "decisions": [],
        }
        update = asyncio.run(main_reason(state))
        self.assertEqual(update["stop_reason"], "budget_exhausted")
        self.assertEqual(update["decision"].action, "finish")

    def test_tool_failure_becomes_structured_failed_observation(self):
        input_data = make_input(tools=("dimension_period_comparison",))
        state: DimensionAttributionState = {
            "input": input_data,
            "request_id": input_data.request.request_id,
            "decision": AttributionDecision(
                action="execute_tool", tool_name="dimension_period_comparison",
                dimension_column="dim_store.city", reason="test",
            ),
            "observations": [],
        }

        async def fail(*args, **kwargs):
            raise TimeoutError("synthetic tool timeout")

        with mock.patch.object(ATTRIBUTION_TOOL_REGISTRY["dimension_period_comparison"], "run", fail):
            update = asyncio.run(execute_attribution_tools(state))
        self.assertEqual(update["observations"][0].status, "failed")
        self.assertEqual(update["stop_reason"], "execution_failed")
        self.assertIn("synthetic tool timeout", update["observations"][0].error)

    def _input_with_dimensions(self):
        input_data = make_input(tools=("dimension_period_comparison",))
        input_data.raw_question = "为什么销售额变化"
        input_data.metric_context.dimensions.append(MetricDimension(name="渠道", column="dim_store.channel_type"))
        return input_data

    @staticmethod
    def _llm_json(payload):
        return RunnableLambda(lambda _: payload)

    def test_llm_rejects_dimension_outside_meta(self):
        input_data = self._input_with_dimensions()
        state: DimensionAttributionState = {"input": input_data, "observations": [], "decisions": []}
        with mock.patch(
            "app.agent.anomaly_agent.DimensionAttribution_Agent.nodes.get_context_llm",
            return_value=self._llm_json(
                '{"action":"execute_tool","tool_name":"dimension_period_comparison",'
                '"dimension_column":"dim_order.unknown","depth":0,"reason":"bad"}'
            ),
        ):
            update = asyncio.run(main_reason(state, SimpleNamespace(context={"llm_provider": "test"})))
        self.assertEqual(update["stop_reason"], "illegal_decision")

    def test_llm_rejects_unobserved_drill_member(self):
        input_data = self._input_with_dimensions()
        observation = AttributionObservation(
            observation_id="obs", tool_name="dimension_period_comparison", status="ok",
            dimension_column="dim_store.channel_type",
            evidence={"members": [{"group_value": "线上商城"}]},
        )
        state: DimensionAttributionState = {
            "input": input_data, "round_count": 1, "current_depth": 1,
            "observations": [observation], "decisions": [],
            "analyzed_dimensions": ["dim_store.channel_type"],
        }
        with mock.patch(
            "app.agent.anomaly_agent.DimensionAttribution_Agent.nodes.get_context_llm",
            return_value=self._llm_json(
                '{"action":"execute_tool","tool_name":"dimension_period_comparison",'
                '"dimension_column":"dim_store.city","depth":1,'
                '"parent_scope":[{"dimension_column":"dim_store.channel_type","member_value":"虚构渠道"}],'
                '"reason":"drill"}'
            ),
        ):
            update = asyncio.run(main_reason(state, SimpleNamespace(context={"llm_provider": "test"})))
        self.assertEqual(update["stop_reason"], "insufficient_evidence")

    def test_llm_accepts_observed_member_and_builds_unique_task(self):
        input_data = self._input_with_dimensions()
        observation = AttributionObservation(
            observation_id="obs", tool_name="dimension_period_comparison", status="ok",
            dimension_column="dim_store.channel_type",
            evidence={"members": [{"group_value": "线上商城"}]},
        )
        scope = DimensionScope(dimension_column="dim_store.channel_type", member_value="线上商城")
        state: DimensionAttributionState = {
            "input": input_data, "round_count": 1, "current_depth": 1,
            "observations": [observation], "decisions": [],
            "analyzed_dimensions": ["dim_store.channel_type"], "analyzed_task_ids": [],
        }
        with mock.patch(
            "app.agent.anomaly_agent.DimensionAttribution_Agent.nodes.get_context_llm",
            return_value=self._llm_json(
                '{"action":"execute_tool","tool_name":"dimension_period_comparison",'
                '"dimension_column":"dim_store.city","depth":1,'
                '"parent_scope":[{"dimension_column":"dim_store.channel_type","member_value":"线上商城"}],'
                '"reason":"drill"}'
            ),
        ):
            update = asyncio.run(main_reason(state, SimpleNamespace(context={"llm_provider": "test"})))
        self.assertEqual(update["decision"].action, "execute_tool")
        self.assertTrue(update["decision"].task_id)
        self.assertEqual(update["decision"].parent_scope, [scope])

    def test_llm_failure_stops_without_query(self):
        input_data = self._input_with_dimensions()
        state: DimensionAttributionState = {"input": input_data, "observations": [], "decisions": []}
        with mock.patch(
            "app.agent.anomaly_agent.DimensionAttribution_Agent.nodes.get_context_llm",
            side_effect=RuntimeError("LLM unavailable"),
        ):
            update = asyncio.run(main_reason(state, SimpleNamespace(context={"llm_provider": "test"})))
        self.assertEqual(update["stop_reason"], "decision_failed")
        self.assertEqual(update["decision"].action, "finish")

    def test_fixed_dimension_determines_region_so_region_is_not_candidate(self):
        input_data = self._input_with_dimensions()
        input_data.request.filters = [FilterCondition(dimension="城市", value="北京")]
        input_data.metric_context.dimensions[0].determines = ["dim_store.region"]
        input_data.metric_context.dimensions.append(
            MetricDimension(name="大区", column="dim_store.region")
        )
        candidates, error = _candidate_dimensions({"input": input_data})
        self.assertIsNone(error)
        self.assertEqual([item.column for item in candidates], ["dim_store.channel_type"])


if __name__ == "__main__":
    unittest.main()
