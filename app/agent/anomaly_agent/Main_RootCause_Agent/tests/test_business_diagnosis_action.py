"""RootCause owns diagnosis decisions; only a scoped tool is registered."""
import json
import unittest

from app.agent.anomaly_agent.Main_RootCause_Agent.diagnosis_support.schemas import evidence_id
from app.agent.anomaly_agent.Main_RootCause_Agent.diagnosis_support.tests.test_skeleton import make_business_input
from app.agent.anomaly_agent.Main_RootCause_Agent.actions.registry import ACTION_REGISTRY
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.action_executor import action_executor
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.supervisor_reason import _specialist_preference
from app.agent.anomaly_agent.Main_RootCause_Agent.tests.test_dimension_attribution_action import attribution_state
from app.tests.test_supervisor_react import runtime


def scoped_state(*, ready=True, member="线下旗舰店", parent_id=None):
    state = attribution_state(ready=ready, task_type="hypothesis_analysis")
    data = make_business_input()
    path = data.attribution_result.paths[0]
    if ready:
        state["observations"].append({"step": 2, "action_name": "dimension_attribution", "status": "completed",
                                      "attribution_result": data.attribution_result.model_dump(mode="json"),
                                      "evidence": {"total_query_count": 2}, "dedupe_key": "dimension"})
    state["supervisor_decision"] = {"action": "call_tool", "tool_name": "scoped_metric_decomposition", "reason": "verify",
                                    "tool_arguments": {"dimension_column": path.dimension_column, "member_value": member,
                                                       "parent_evidence_id": parent_id or evidence_id("dimension_attribution", path.model_dump(mode="json")),
                                                       "task_id": path.task_id}}
    return state


class BusinessDiagnosisMigrationTest(unittest.IsolatedAsyncioTestCase):
    def test_scoped_tool_is_registered_without_child_graph(self):
        self.assertIn("scoped_metric_decomposition", ACTION_REGISTRY)
        self.assertNotIn("business_diagnosis", ACTION_REGISTRY)
        self.assertEqual(_specialist_preference("分析渠道贡献、订单量与客单价，并进一步诊断", None),
                         ["dimension_attribution", "metric_decomposition", "diagnosis"])

    async def test_no_ready_observation_is_blocked(self):
        result = await action_executor(scoped_state(ready=False), runtime())
        self.assertEqual(result["stop_reason"], "verification_not_ready")

    async def test_fabricated_parent_or_member_is_rejected(self):
        for state in (scoped_state(parent_id="ev_fabricated"), scoped_state(member="虚构门店")):
            result = await action_executor(state, runtime())
            self.assertNotEqual(result["observations"][-1]["status"], "completed")

    async def test_duplicate_and_budget_are_enforced(self):
        state = scoped_state()
        args = state["supervisor_decision"]["tool_arguments"]
        signature = json.dumps(args, sort_keys=True, ensure_ascii=False)
        state["observations"].append({"step": 3, "action_name": "scoped_metric_decomposition", "status": "completed", "dedupe_key": signature})
        result = await action_executor(state, runtime())
        self.assertEqual(result["stop_reason"], "duplicate_action")
        state = scoped_state()
        state["observations"][1]["attribution_result"]["total_query_count"] = 23
        result = await action_executor(state, runtime())
        self.assertEqual(result["stop_reason"], "sql_budget_exhausted")


if __name__ == "__main__":
    unittest.main()
