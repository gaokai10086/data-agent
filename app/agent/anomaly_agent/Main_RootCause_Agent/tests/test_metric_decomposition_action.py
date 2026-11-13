"""RootCause 到 MetricDecompositionAgent 的控制流契约测试。"""

import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from app.agent.anomaly_agent.Main_RootCause_Agent.actions.registry import (
    ACTION_REGISTRY, ActionSpec, DecompositionExecution,
)
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.action_executor import (
    _action_signature, _build_analysis_request, action_executor,
)
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.supervisor_reason import supervisor_reason
from app.agent.anomaly_agent.Main_RootCause_Agent.graph import route_after_action
from langgraph.constants import END
from app.agent.anomaly_agent.MetricDecomposition_Agent.schemas import DecompositionResult
from app.agent.anomaly_agent.MetricDecomposition_Agent.tests.test_skeleton import make_input
from app.agent.anomaly_agent.Main_RootCause_Agent.tests.test_dimension_attribution_action import attribution_state
from app.tests.test_supervisor_react import runtime


class MetricDecompositionActionTest(unittest.IsolatedAsyncioTestCase):
    def _state(self):
        state = attribution_state(task_type="metric_decomposition")
        state["raw_question"] = "为什么销售额下降？是订单量还是客单价造成的？"
        state["supervisor_decision"] = {
            "action": "call_tool", "tool_name": "metric_decomposition",
            "reason": "factor analysis",
        }
        return state

    async def test_ready_input_and_result_are_forwarded(self):
        child = DecompositionResult(
            request_id="req-1", status="completed", completion_status="complete",
            reason="completed", stop_reason="completed", summary="ok",
        )
        executor = AsyncMock(return_value=DecompositionExecution(
            result=child, nodes=["main_reason", "execute_decomposition_tools", "finalize_decomposition"],
        ))
        spec = ActionSpec("metric_decomposition", "metric_decomposition", "metric_decomposition", executor)
        with patch.dict(ACTION_REGISTRY, {"metric_decomposition": spec}):
            result = await action_executor(self._state(), runtime())
        self.assertEqual(executor.await_args.args[0].inspection_outcome.status, "ready")
        self.assertEqual(result["observations"][-1]["status"], "completed")
        self.assertIn("decomposition_result", result["observations"][-1])

    async def test_missing_ready_observation_is_blocked(self):
        state = self._state()
        state["observations"] = []
        result = await action_executor(state, runtime())
        self.assertEqual(result["stop_reason"], "verification_not_ready")
        self.assertFalse(result["observations"][-1]["tool_called"])

    async def test_supervisor_prefers_decomposition_for_factor_question(self):
        state = self._state()
        decision = await supervisor_reason(state, runtime())
        self.assertEqual(decision["supervisor_decision"]["tool_name"], "metric_decomposition")

    async def test_partial_result_is_preserved_and_can_continue_to_dimension(self):
        child = DecompositionResult(
            request_id="req-1", status="completed", completion_status="partial",
            reason="completed", stop_reason="budget_exhausted", summary="partial",
        )
        executor = AsyncMock(return_value=DecompositionExecution(result=child, nodes=["finalize_decomposition"]))
        spec = ActionSpec("metric_decomposition", "metric_decomposition", "metric_decomposition", executor)
        state = self._state()
        state["raw_question"] = "分析渠道贡献以及订单量和客单价因素"
        with patch.dict(ACTION_REGISTRY, {"metric_decomposition": spec}):
            result = await action_executor(state, runtime())
        self.assertEqual(result["observations"][-1]["status"], "partial")
        state["observations"] = result["observations"]
        next_decision = await supervisor_reason(state, runtime())
        self.assertEqual(next_decision["supervisor_decision"]["tool_name"], "dimension_attribution")

    async def test_failed_result_stops_with_structured_error(self):
        child = DecompositionResult(
            request_id="req-1", status="failed", completion_status="none",
            reason="execution_failed", stop_reason="execution_failed", summary="failed",
        )
        executor = AsyncMock(return_value=DecompositionExecution(result=child, nodes=["finalize_decomposition"]))
        spec = ActionSpec("metric_decomposition", "metric_decomposition", "metric_decomposition", executor)
        with patch.dict(ACTION_REGISTRY, {"metric_decomposition": spec}):
            result = await action_executor(self._state(), runtime())
        self.assertEqual(result["observations"][-1]["status"], "failed")
        state = self._state()
        state["observations"] = result["observations"]
        decision = await supervisor_reason(state, runtime())
        self.assertEqual(decision["supervisor_decision"]["action"], "stop")

    def test_rejected_change_does_not_route_to_specialist(self):
        state = self._state()
        state["observations"] = [{
            "step": 1, "action_name": "change_inspection", "status": "rejected",
            "reason": "direction_mismatch", "tool_called": True,
        }]
        self.assertEqual(route_after_action(state), END)


if __name__ == "__main__":
    unittest.main()
