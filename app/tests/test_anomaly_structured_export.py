"""The anomaly route emits Main RootCause data without the retired report UI contract."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.agent.anomaly_agent.Main_RootCause_Agent.analysis_result import AnalysisResult
from app.agent.orchestrator.nodes.run_anomaly_agent import run_anomaly_agent


class StructuredExportTest(unittest.IsolatedAsyncioTestCase):
    async def test_final_event_contains_main_result_without_report(self):
        result = AnalysisResult(request_id="request-1", metric="销售额", target_period="2026-08",
                                comparison_period="2026-07", completion_status="complete")
        state = {"skill_result": {"status": "matched", "skill_name": "sales_amount_anomaly"},
                 "analysis_result": result.model_dump(mode="json"), "observations": []}

        async def stream(*_args, **_kwargs):
            yield "values", state

        events = []
        runtime = SimpleNamespace(context={}, stream_writer=events.append)
        with patch("app.agent.orchestrator.nodes.run_anomaly_agent.main_root_cause_agent",
                   SimpleNamespace(astream=stream)):
            output = await run_anomaly_agent({"query": "为什么2026年8月销售额比7月下降了？"}, runtime)
        self.assertIs(output["agent_result"], state)
        final = next(item for item in events if item.get("type") == "anomaly_result")
        self.assertEqual(final["analysis_result"]["request_id"], "request-1")
        self.assertEqual(final["report_input"]["analysis_result"]["completion_status"], "complete")
        self.assertNotIn("report", final)


if __name__ == "__main__":
    unittest.main()
