"""run_anomaly_agent Skill 入口适配测试。"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

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


def build_result(status: str = "matched") -> dict[str, object]:
    return {
        "raw_question": "为什么销售额下降？",
        "extracted_metrics": ["销售额"],
        "skill_result": {
            "status": status,
            "reason": "指标全部由同一 Skill 支持",
            "skill_name": "sales_amount_anomaly" if status == "matched" else None,
        },
    }


class RunAnomalyAgentTest(unittest.IsolatedAsyncioTestCase):
    async def call_with_result(self, result):
        runtime = FakeRuntime()
        with patch(
            "app.agent.orchestrator.nodes.run_anomaly_agent.main_root_cause_agent.ainvoke",
            new=AsyncMock(return_value=result),
        ):
            output = await run_anomaly_agent(
                {
                    "query": "为什么销售额下降？",
                    "rewritten_query": "为什么销售额下降？",
                    "intent": "anomaly_analysis",
                },
                runtime,
            )
        return output, runtime.events

    async def test_matched_forwards_skill_match_result_and_skill(self):
        result = build_result("matched")
        output, events = await self.call_with_result(result)

        self.assertEqual(output, {"agent_result": result})
        self.assertEqual(
            [event["type"] for event in events[1:]],
            ["skill_match_result", "selected_skill"],
        )
        self.assertEqual(events[1]["status"], "matched")
        self.assertEqual(events[2]["skill_name"], "sales_amount_anomaly")

    async def test_business_unsupported_is_not_technical_failure(self):
        result = build_result("unsupported")
        output, events = await self.call_with_result(result)

        self.assertEqual(output, {"agent_result": result})
        self.assertEqual(
            [event.get("type") for event in events],
            ["progress", "skill_match_result"],
        )
        self.assertNotIn("error_response", [event.get("type") for event in events])

    async def test_input_maps_only_raw_question(self):
        seen = {}

        async def fake_ainvoke(input, context):
            seen.update(input=input, context=context)
            return build_result()

        with patch(
            "app.agent.orchestrator.nodes.run_anomaly_agent.main_root_cause_agent.ainvoke",
            new=AsyncMock(side_effect=fake_ainvoke),
        ):
            await run_anomaly_agent(
                {"query": "原问题", "rewritten_query": "改写后的问题"},
                FakeRuntime(),
            )

        self.assertEqual(seen["input"], {"raw_question": "改写后的问题"})

    async def test_missing_status_is_technical_failure(self):
        result = build_result()
        result.pop("skill_result")
        output, events = await self.call_with_result(result)

        failure = output["agent_result"]["preparation_result"]
        self.assertEqual(failure.status, "failed")
        self.assertEqual(failure.reason, "missing_skill_result")
        self.assertEqual(
            [event.get("type") for event in events],
            ["progress", "error_response"],
        )

    async def test_ainvoke_exception_returns_structured_failure_without_leak(self):
        runtime = FakeRuntime()
        internal_error = "postgresql://admin:secret@db/internal"
        with patch(
            "app.agent.orchestrator.nodes.run_anomaly_agent.main_root_cause_agent.ainvoke",
            new=AsyncMock(side_effect=RuntimeError(internal_error)),
        ), patch(
            "app.agent.orchestrator.nodes.run_anomaly_agent.logger.exception"
        ) as log_exception:
            output = await run_anomaly_agent(
                {"query": "为什么销售额下降？"},
                runtime,
            )

        failure = output["agent_result"]["preparation_result"]
        self.assertEqual(failure.status, "failed")
        self.assertEqual(failure.reason, "anomaly_execution_error")
        self.assertEqual(failure.user_message, TECHNICAL_FAILURE_MESSAGE)
        serialized = json.dumps(runtime.events, ensure_ascii=False, default=str)
        self.assertNotIn("postgresql", serialized)
        self.assertNotIn("secret", serialized)
        log_exception.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
