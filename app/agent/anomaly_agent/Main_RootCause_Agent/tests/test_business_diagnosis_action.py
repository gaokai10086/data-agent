"""RootCause 到 BusinessDiagnosisAgent 的控制流契约测试。"""

import unittest
from unittest.mock import AsyncMock, patch

from app.agent.anomaly_agent.BusinessDiagnosis_Agent.schemas import DiagnosisResult
from app.agent.anomaly_agent.Main_RootCause_Agent.actions.registry import (
    ACTION_REGISTRY, ActionSpec, BusinessDiagnosisExecution,
)
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.action_executor import action_executor
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.supervisor_reason import _specialist_preference
from app.agent.anomaly_agent.Main_RootCause_Agent.tests.test_dimension_attribution_action import attribution_state
from app.tests.test_supervisor_react import runtime


class BusinessDiagnosisActionTest(unittest.IsolatedAsyncioTestCase):
    def test_action_is_registered(self):
        self.assertIn("business_diagnosis", ACTION_REGISTRY)
        self.assertEqual(ACTION_REGISTRY["business_diagnosis"].task_type, "hypothesis_analysis")

    def test_comprehensive_question_orders_business_diagnosis_last(self):
        self.assertEqual(
            _specialist_preference("分析渠道贡献、订单量与客单价，并进一步诊断主要影响渠道的下降因素。", None),
            ["dimension_attribution", "metric_decomposition", "business_diagnosis"],
        )

    async def test_ready_context_is_forwarded(self):
        state = attribution_state(task_type="hypothesis_analysis")
        state["raw_question"] = "进一步诊断主要影响渠道的下降因素"
        state["supervisor_decision"] = {
            "action": "call_tool", "tool_name": "business_diagnosis", "reason": "diagnose",
        }
        child = DiagnosisResult(
            request_id="req-1", status="completed", completion_status="complete",
            findings=[], hypotheses=[], evidence_ids=[], stop_reason="completed", summary="ok",
        )
        executor = AsyncMock(return_value=BusinessDiagnosisExecution(
            result=child, nodes=["prepare_evidence", "main_reason", "finalize_diagnosis"],
        ))
        spec = ActionSpec("business_diagnosis", "business_diagnosis", "hypothesis_analysis", executor)
        with patch.dict(ACTION_REGISTRY, {"business_diagnosis": spec}):
            result = await action_executor(state, runtime())
        child_input = executor.await_args.args[0]
        self.assertEqual(child_input.inspection_outcome.status, "ready")
        self.assertEqual(child_input.analysis_request.request_id, "req-1")
        self.assertEqual(result["observations"][-1]["status"], "completed")
        self.assertIn("business_diagnosis_result", result["observations"][-1])

    async def test_missing_ready_observation_is_blocked(self):
        state = attribution_state(ready=False, task_type="hypothesis_analysis")
        state["supervisor_decision"] = {
            "action": "call_tool", "tool_name": "business_diagnosis", "reason": "diagnose",
        }
        result = await action_executor(state, runtime())
        self.assertEqual(result["stop_reason"], "verification_not_ready")
        self.assertFalse(result["observations"][-1]["tool_called"])


if __name__ == "__main__":
    unittest.main()
