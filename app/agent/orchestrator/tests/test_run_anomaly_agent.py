"""run_anomaly_agent 异常与结果契约测试，可直接用 Python 运行。"""

from __future__ import annotations

import json
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
from app.agent.orchestrator.nodes.run_anomaly_agent import (
    TECHNICAL_FAILURE_MESSAGE,
    run_anomaly_agent,
)


class FakeRuntime:
    def __init__(self):
        self.context = {}
        self.events: list[dict[str, object]] = []

    def stream_writer(self, event: dict[str, object]) -> None:
        self.events.append(event)


def build_result(status: str) -> dict[str, object]:
    request = AnalysisRequest(
        metric="销售额",
        target_period="2025-07",
        comparison_period="2025-06",
        comparison_type="mom",
        expected_direction="decrease",
    )
    if status == "ready":
        verification = ChangeVerification(
            decision="continue",
            reason="change_confirmed",
            expected_direction="decrease",
            actual_direction="decrease",
            summary="销售额从100下降为80。",
            warnings=["核验通过"],
        )
    else:
        verification = ChangeVerification(
            decision="stop",
            reason="no_change",
            expected_direction="decrease",
            actual_direction="unchanged",
            summary="销售额未发生变化。",
        )
    preparation = PreparationResult(
        status=status,
        reason=verification.reason,
        request_id=request.request_id,
        user_message=verification.summary,
    )
    return {
        "analysis_request": request,
        "change_verification": verification,
        "preparation_result": preparation,
    }


class RunAnomalyAgentTest(unittest.IsolatedAsyncioTestCase):
    async def call_with_result(self, result):
        runtime = FakeRuntime()
        with patch(
            "app.agent.orchestrator.nodes.run_anomaly_agent.anomaly_graph.ainvoke",
            new=AsyncMock(return_value=result),
        ):
            output = await run_anomaly_agent(
                {"query": "为什么销售额下降？", "intent": "anomaly_analysis"},
                runtime,
            )
        return output, runtime.events

    async def test_ready_keeps_existing_events_and_complete_result(self):
        result = build_result("ready")
        output, events = await self.call_with_result(result)

        self.assertEqual(output, {"agent_result": result})
        self.assertEqual(
            [event["type"] for event in events[1:]],
            [
                "anomaly_preparation_result",
                "anomaly_request",
                "change_verification",
                "token",
            ],
        )
        self.assertEqual(events[1]["status"], "ready")
        self.assertEqual(events[3]["decision"], "continue")
        self.assertNotIn("error_response", [event.get("type") for event in events])

    async def test_rejected_remains_business_stop(self):
        result = build_result("rejected")
        output, events = await self.call_with_result(result)

        self.assertEqual(output, {"agent_result": result})
        self.assertEqual(
            [event["type"] for event in events[1:]],
            [
                "anomaly_preparation_result",
                "anomaly_request",
                "change_verification",
                "token",
            ],
        )
        self.assertEqual(events[1]["status"], "rejected")
        self.assertEqual(events[3]["decision"], "stop")
        self.assertEqual(events[-1]["content"], "销售额未发生变化。")
        self.assertNotIn("error_response", [event.get("type") for event in events])

    async def test_ainvoke_exception_returns_structured_failure_without_leak(self):
        runtime = FakeRuntime()
        internal_error = "postgresql://admin:secret@db/internal"
        with patch(
            "app.agent.orchestrator.nodes.run_anomaly_agent.anomaly_graph.ainvoke",
            new=AsyncMock(side_effect=RuntimeError(internal_error)),
        ), patch(
            "app.agent.orchestrator.nodes.run_anomaly_agent.logger.exception"
        ) as log_exception:
            output = await run_anomaly_agent(
                {"query": "为什么销售额下降？", "intent": "anomaly_analysis"},
                runtime,
            )

        failure = output["agent_result"]["preparation_result"]
        self.assertEqual(failure.status, "failed")
        self.assertEqual(failure.reason, "anomaly_execution_error")
        self.assertEqual(failure.user_message, TECHNICAL_FAILURE_MESSAGE)
        self.assertEqual(
            [event.get("type") for event in runtime.events],
            ["progress", "error_response"],
        )
        self.assertEqual(
            sum(event.get("type") == "error_response" for event in runtime.events),
            1,
        )
        serialized = json.dumps(runtime.events, ensure_ascii=False, default=str)
        self.assertNotIn("postgresql", serialized)
        self.assertNotIn("secret", serialized)
        log_exception.assert_called_once()

    async def test_missing_preparation_result_is_technical_failure(self):
        result = build_result("ready")
        result.pop("preparation_result")
        output, events = await self.call_with_result(result)

        failure = output["agent_result"]["preparation_result"]
        self.assertEqual(failure.status, "failed")
        self.assertEqual(failure.reason, "missing_preparation_result")
        self.assertEqual(
            [event.get("type") for event in events],
            ["progress", "error_response"],
        )

    async def test_missing_analysis_request_is_technical_failure(self):
        result = build_result("ready")
        result.pop("analysis_request")
        output, events = await self.call_with_result(result)

        failure = output["agent_result"]["preparation_result"]
        self.assertEqual(failure.status, "failed")
        self.assertEqual(failure.reason, "missing_analysis_request")
        self.assertEqual(
            [event.get("type") for event in events],
            ["progress", "error_response"],
        )

    async def test_missing_change_verification_is_technical_failure(self):
        result = build_result("ready")
        result.pop("change_verification")
        output, events = await self.call_with_result(result)

        failure = output["agent_result"]["preparation_result"]
        self.assertEqual(failure.status, "failed")
        self.assertEqual(failure.reason, "missing_change_verification")
        self.assertEqual(
            [event.get("type") for event in events],
            ["progress", "error_response"],
        )

    async def test_preparation_failed_is_technical_failure(self):
        result = build_result("ready")
        result["preparation_result"] = PreparationResult(
            status="failed",
            reason="missing_verification",
            user_message="内部前置失败",
        )
        output, events = await self.call_with_result(result)

        failure = output["agent_result"]["preparation_result"]
        self.assertEqual(failure.status, "failed")
        self.assertEqual(failure.reason, "missing_verification")
        self.assertEqual(failure.user_message, TECHNICAL_FAILURE_MESSAGE)
        self.assertEqual(
            [event.get("type") for event in events],
            ["progress", "error_response"],
        )
        self.assertEqual(
            sum(event.get("type") == "error_response" for event in events),
            1,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
