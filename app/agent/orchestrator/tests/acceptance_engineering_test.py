"""应用层工程验收：路由、父子 State 映射、技术失败与 SSE 事件转发。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.anomaly_agent.schemas import (
    AnalysisRequest,
    ChangeVerification,
    PreparationResult,
)
from app.agent.orchestrator.graph import route_intent
from app.agent.orchestrator.nodes.run_anomaly_agent import run_anomaly_agent
from app.agent.orchestrator.nodes.run_data_agent import run_data_agent


class AcceptanceEngineeringTest(unittest.IsolatedAsyncioTestCase):
    async def test_route_intent_maps_three_branches_and_rejects_unknown(self):
        self.assertEqual(route_intent({"intent": "query_database"}), "query_database")
        self.assertEqual(route_intent({"intent": "anomaly_analysis"}), "anomaly_analysis")
        self.assertEqual(route_intent({"intent": "direct_answer"}), "direct_answer")
        with self.assertRaises(ValueError):
            route_intent({"intent": "unknown"})

    async def test_data_agent_maps_rewritten_query_into_child_state(self):
        events = []
        seen = {}

        class FakeDataGraph:
            async def astream(self, *, input, context, stream_mode):
                seen.update(input=input, context=context, stream_mode=stream_mode)
                yield "values", {"sql": "SELECT 1"}

        with patch(
            "app.agent.orchestrator.nodes.run_data_agent.data_agent_graph",
            FakeDataGraph(),
        ):
            result = await run_data_agent(
                {"query": "原问题", "rewritten_query": "改写后的问题"},
                SimpleNamespace(context="ctx", stream_writer=events.append),
            )

        self.assertEqual(seen["input"]["query"], "改写后的问题")
        self.assertEqual(seen["context"], "ctx")
        self.assertEqual(seen["stream_mode"], ["custom", "values"])
        self.assertEqual(result, {"agent_result": {"sql": "SELECT 1"}})
        self.assertIn("stage", events[0])

    async def test_anomaly_agent_maps_state_and_forwards_business_events(self):
        request = AnalysisRequest(
            metric="销售额",
            target_period="2025-07",
            comparison_period="2025-06",
            comparison_type="mom",
        )
        verification = ChangeVerification(
            decision="continue",
            reason="change_confirmed",
            expected_direction="decrease",
            actual_direction="decrease",
            summary="核验通过",
        )
        preparation = PreparationResult(
            status="ready",
            reason="change_confirmed",
            request_id=request.request_id,
            user_message="核验通过",
        )
        child_result = {
            "analysis_request": request,
            "change_verification": verification,
            "preparation_result": preparation,
        }
        seen = {}

        async def fake_ainvoke(input, context):
            seen.update(input=input, context=context)
            return child_result

        events = []
        with patch(
            "app.agent.orchestrator.nodes.run_anomaly_agent.anomaly_graph.ainvoke",
            new=AsyncMock(side_effect=fake_ainvoke),
        ):
            result = await run_anomaly_agent(
                {
                    "query": "原问题",
                    "rewritten_query": "改写后的问题",
                    "intent": "anomaly_analysis",
                },
                SimpleNamespace(context="ctx", stream_writer=events.append),
            )

        self.assertEqual(
            seen["input"],
            {"raw_question": "改写后的问题", "intent": "anomaly_analysis"},
        )
        self.assertEqual(seen["context"], "ctx")
        self.assertEqual(result, {"agent_result": child_result})
        event_types = [event.get("type", "progress") for event in events]
        self.assertEqual(
            event_types,
            [
                "progress",
                "anomaly_preparation_result",
                "anomaly_request",
                "change_verification",
                "token",
            ],
        )

    async def test_anomaly_technical_failure_returns_failed_result(self):
        events = []
        with patch(
            "app.agent.orchestrator.nodes.run_anomaly_agent.anomaly_graph.ainvoke",
            new=AsyncMock(side_effect=RuntimeError("db down")),
        ), patch(
            "app.agent.orchestrator.nodes.run_anomaly_agent.logger.exception"
        ):
            result = await run_anomaly_agent(
                {"query": "为什么下降？", "intent": "anomaly_analysis"},
                SimpleNamespace(context="ctx", stream_writer=events.append),
            )

        preparation = result["agent_result"]["preparation_result"]
        self.assertEqual(preparation.status, "failed")
        self.assertEqual(
            [event.get("type") for event in events],
            ["progress", "error_response"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
