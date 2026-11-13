"""RootCause 到独立维度归因 Graph 的控制流契约测试。"""

import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from app.agent.anomaly_agent.DimensionAttribution_Agent.schemas import (
    AttributionPath, AttributionResult, DimensionMemberAttribution, ReconciliationResult,
)
from app.agent.anomaly_agent.DimensionAttribution_Agent.tests.test_skeleton import make_input
from app.agent.anomaly_agent.Main_RootCause_Agent.actions.registry import (
    ACTION_REGISTRY, ActionSpec, AttributionExecution,
)
from app.agent.anomaly_agent.Main_RootCause_Agent.graph import route_to_supervisor
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.action_executor import (
    _action_signature, _build_analysis_request, action_executor,
)
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.supervisor_reason import supervisor_reason
from app.tests.test_supervisor_react import base_state, runtime


def attribution_state(*, ready=True, task_type="dimension_attribution"):
    state = base_state()
    state["raw_question"] = "为什么 2026 年 8 月北京销售额相比 7 月下降？"
    state["schema_result"]["task_type"] = task_type
    state["parsed_result"]["parsed_request"] = {
        "metric": "销售额", "target_period": {"year": 2026, "month": 8},
        "comparison_period": {"year": 2026, "month": 7},
        "filters": [], "expected_direction": "decrease",
    }
    state["supervisor_decision"]["tool_name"] = "dimension_attribution"
    if ready:
        request = _build_analysis_request(state["parsed_result"]["parsed_request"])
        inspection = make_input().inspection_outcome
        state["observations"] = [{
            "step": 1, "action_name": "change_inspection", "status": "ready",
            "reason": "change_confirmed", "tool_called": True,
            "dedupe_key": _action_signature("change_inspection", request),
            "inspection_outcome": inspection.model_dump(mode="json"),
            "evidence": {"change_amount": "-1"},
        }]
    return state


class DimensionAttributionActionTest(unittest.IsolatedAsyncioTestCase):
    async def test_dimension_schema_enters_supervisor(self):
        self.assertEqual(route_to_supervisor(attribution_state()), "supervisor_reason")

    async def test_attribution_requires_ready_inspection(self):
        state = attribution_state(ready=False)
        result = await action_executor(state, runtime())
        self.assertEqual(result["stop_reason"], "verification_not_ready")
        self.assertFalse(result["observations"][-1]["tool_called"])

    async def test_mismatched_inspection_evidence_is_rejected(self):
        state = attribution_state()
        state["observations"][0]["inspection_outcome"]["overall_result"]["target_period"] = "2026-09"
        result = await action_executor(state, runtime())
        self.assertEqual(result["stop_reason"], "invalid_inspection_evidence")
        self.assertFalse(result["observations"][-1]["tool_called"])

    async def test_ready_inspection_passes_trusted_input(self):
        child = AttributionResult(
            request_id="req-1", status="completed", completion_status="complete",
            reason="completed", stop_reason="completed", summary="ok",
        )
        executor = AsyncMock(return_value=AttributionExecution(result=child, nodes=["main_reason", "finalize_attribution"]))
        spec = ActionSpec("dimension_attribution", "dimension_attribution", "dimension_attribution", executor)
        with patch.dict(ACTION_REGISTRY, {"dimension_attribution": spec}):
            result = await action_executor(attribution_state(), runtime())
        input_data = executor.await_args.args[0]
        self.assertEqual(input_data.inspection_outcome.status, "ready")
        self.assertEqual(input_data.request.request_id, "req-1")
        self.assertEqual(result["observations"][-1]["status"], "completed")

    async def test_ready_inspection_routes_to_attribution_without_reverification(self):
        state = attribution_state()
        state["supervisor_decision"] = {"action": "call_tool", "tool_name": "change_inspection"}
        decision = await supervisor_reason(state, runtime())
        self.assertEqual(decision["supervisor_decision"]["tool_name"], "dimension_attribution")

    async def test_partial_child_evidence_is_preserved_and_explained(self):
        reconciliation = ReconciliationResult(
            comparison_sum=Decimal("2"), current_sum=Decimal("1"),
            summed_change_amount=Decimal("-1"), overall_comparison_value=Decimal("2"),
            overall_current_value=Decimal("1"), overall_change_amount=Decimal("-1"),
            comparison_matches=True, current_matches=True, change_matches=True,
            tolerance=Decimal("0.01"),
        )
        path = AttributionPath(
            task_id="prior-evidence", depth=0, dimension_column="dim_store.city",
            attributions=[DimensionMemberAttribution(
                group_value="sample", comparison_value=Decimal("2"),
                current_value=Decimal("1"), change_amount=Decimal("-1"),
                net_contribution_rate=Decimal("1"),
            )], reconciliation=reconciliation,
        )
        child = AttributionResult(
            request_id="req-1", status="completed", completion_status="partial",
            reason="completed", stop_reason="budget_exhausted", summary="partial",
            paths=[path], total_query_count=2,
        )
        executor = AsyncMock(return_value=AttributionExecution(result=child, nodes=["main_reason", "finalize_attribution"]))
        spec = ActionSpec("dimension_attribution", "dimension_attribution", "dimension_attribution", executor)
        with patch.dict(ACTION_REGISTRY, {"dimension_attribution": spec}):
            state = attribution_state()
            result = await action_executor(state, runtime())
        observation = result["observations"][-1]
        self.assertEqual(observation["status"], "partial")
        self.assertEqual(observation["attribution_stop_reason"], "budget_exhausted")
        self.assertEqual(observation["evidence"]["paths"][0]["task_id"], "prior-evidence")
        self.assertEqual(observation["evidence"]["total_query_count"], 2)
        state["observations"] = result["observations"]
        reason = await supervisor_reason(state, runtime())
        self.assertIn("attribution_partial", reason["supervisor_decision"]["reason"])

    async def test_failed_child_is_structured(self):
        child = AttributionResult(
            request_id="req-1", status="failed", completion_status="partial",
            reason="execution_failed", stop_reason="execution_failed", summary="failed",
        )
        executor = AsyncMock(return_value=AttributionExecution(result=child, nodes=["main_reason", "finalize_attribution"]))
        spec = ActionSpec("dimension_attribution", "dimension_attribution", "dimension_attribution", executor)
        with patch.dict(ACTION_REGISTRY, {"dimension_attribution": spec}):
            state = attribution_state()
            result = await action_executor(state, runtime())
        self.assertEqual(result["observations"][-1]["status"], "failed")
        state["observations"] = result["observations"]
        reason = await supervisor_reason(state, runtime())
        self.assertEqual(reason["supervisor_decision"]["action"], "stop")
