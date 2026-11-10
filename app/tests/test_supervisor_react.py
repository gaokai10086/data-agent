"""Main Supervisor ReAct 第一阶段确定性控制流测试。"""

from __future__ import annotations

import json
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from app.agent.anomaly_agent.Main_RootCause_Agent.actions.registry import (
    ACTION_REGISTRY,
    ActionSpec,
)
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.action_executor import action_executor
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.supervisor_reason import supervisor_reason


def outcome(status: str, reason: str = "test"):
    return SimpleNamespace(
        status=status,
        reason=reason,
        metric_context=SimpleNamespace(metric_key="sales_amount", name="销售额"),
        overall_result=SimpleNamespace(
            target_period="2025-07",
            comparison_period="2025-06",
            current_value=Decimal("80"),
            comparison_value=Decimal("100"),
            change_amount=Decimal("-20"),
            change_rate=Decimal("-0.2"),
        ),
        verification=None,
        model_dump=lambda mode="json": {"status": status, "reason": reason},
    )


def base_state(**overrides):
    state = {
        "raw_question": "为什么2025年7月销售额比6月下降？",
        "extracted_metrics": ["销售额"],
        "skill_result": {
            "status": "matched",
            "skill_name": "sales_amount_anomaly",
            "reason": "指标全部由同一 Skill 支持",
        },
        "schema_result": {
            "status": "matched",
            "task_type": "period_comparison",
            "schema_name": "PeriodComparisonRequest",
            "reason": "双周期比较",
        },
        "parsed_result": {
            "status": "passed",
            "parsed_request": {
                "metric": "销售额",
                "target_period": {"year": 2025, "month": 7},
                "comparison_period": {"year": 2025, "month": 6},
                "filters": [],
                "expected_direction": "decrease",
                "missing_info": [],
            },
            "errors": [],
            "missing_info": [],
        },
        "supervisor_decision": {
            "action": "call_tool",
            "tool_name": "change_inspection",
            "reason": "先验证变化前提",
        },
        "observations": [],
        "step_count": 0,
    }
    state.update(overrides)
    return state


def runtime():
    return SimpleNamespace(context={})


class SupervisorReactTest(unittest.IsolatedAsyncioTestCase):
    async def test_ready_action_is_executed(self):
        mock_tool = AsyncMock(return_value=outcome("ready", "change_confirmed"))
        spec = ActionSpec(
            name="change_inspection",
            capability="change_inspection",
            task_type="period_comparison",
            executor=mock_tool,
        )
        with patch.dict(ACTION_REGISTRY, {"change_inspection": spec}):
            result = await action_executor(base_state(), runtime())

        observation = result["observations"][-1]
        self.assertEqual(observation["status"], "ready")
        self.assertTrue(observation["tool_called"])
        mock_tool.assert_awaited_once()

    async def test_rejected_stops_without_retry(self):
        mock_tool = AsyncMock(return_value=outcome("rejected", "direction_mismatch"))
        spec = ActionSpec("change_inspection", "change_inspection", "period_comparison", mock_tool)
        with patch.dict(ACTION_REGISTRY, {"change_inspection": spec}):
            result = await action_executor(base_state(), runtime())

        self.assertEqual(result["observations"][-1]["status"], "rejected")
        self.assertEqual(result["stop_reason"], "rejected")

    async def test_failed_retries_once_then_blocks(self):
        mock_tool = AsyncMock(side_effect=[outcome("failed"), outcome("failed")])
        spec = ActionSpec("change_inspection", "change_inspection", "period_comparison", mock_tool)
        with patch.dict(ACTION_REGISTRY, {"change_inspection": spec}):
            first = await action_executor(base_state(), runtime())
            second = await action_executor(
                base_state(observations=first["observations"]),
                runtime(),
            )
            third = await action_executor(
                base_state(observations=second["observations"]),
                runtime(),
            )

        self.assertEqual(mock_tool.await_count, 2)
        self.assertEqual(third["observations"][-1]["status"], "blocked")
        self.assertEqual(third["stop_reason"], "retry_exhausted")

    async def test_unregistered_action_is_blocked(self):
        spec = ActionSpec("change_inspection", "change_inspection", "period_comparison", AsyncMock())
        with patch.dict(ACTION_REGISTRY, {"change_inspection": spec}):
            result = await action_executor(
                base_state(supervisor_decision={
                    "action": "call_tool",
                    "tool_name": "not_registered",
                    "reason": "x",
                }),
                runtime(),
            )

        self.assertEqual(result["observations"][-1]["status"], "blocked")
        self.assertFalse(result["observations"][-1]["tool_called"])
        self.assertEqual(result["stop_reason"], "invalid_action")

    async def test_registered_but_unauthorized_action_is_blocked(self):
        spec = ActionSpec(
            name="other",
            capability="data_query",
            task_type="period_comparison",
            executor=AsyncMock(),
        )
        with patch.dict(ACTION_REGISTRY, {"other": spec}):
            result = await action_executor(
                base_state(supervisor_decision={
                    "action": "call_tool",
                    "tool_name": "other",
                    "reason": "x",
                }),
                runtime(),
            )

        self.assertEqual(result["observations"][-1]["status"], "blocked")
        self.assertEqual(result["stop_reason"], "unauthorized_action")
        self.assertFalse(result["observations"][-1]["tool_called"])

    async def test_duplicate_ready_action_is_blocked(self):
        mock_tool = AsyncMock(return_value=outcome("ready"))
        spec = ActionSpec("change_inspection", "change_inspection", "period_comparison", mock_tool)
        with patch.dict(ACTION_REGISTRY, {"change_inspection": spec}):
            first = await action_executor(base_state(), runtime())
            second = await action_executor(
                base_state(observations=first["observations"]),
                runtime(),
            )

        self.assertEqual(mock_tool.await_count, 1)
        self.assertEqual(second["observations"][-1]["status"], "blocked")
        self.assertEqual(second["stop_reason"], "duplicate_action")

    async def test_budget_exhausted_terminates(self):
        observations = [
            {
                "step": i,
                "action_name": "change_inspection",
                "status": "ready",
                "reason": "change_confirmed",
                "tool_called": True,
                "dedupe_key": f"sig-{i}",
                "inspection_outcome": None,
            }
            for i in range(3)
        ]
        result = await supervisor_reason(
            base_state(observations=observations),
            runtime(),
        )

        self.assertEqual(result["supervisor_decision"]["action"], "stop")
        self.assertEqual(result["stop_reason"], "budget_exhausted")

    async def test_second_reason_consumes_ready_observation(self):
        observations = [{
            "step": 1,
            "action_name": "change_inspection",
            "status": "ready",
            "reason": "change_confirmed",
            "tool_called": True,
            "dedupe_key": "sig-1",
            "inspection_outcome": None,
        }]
        fake_llm = RunnableLambda(lambda _: AIMessage(content=json.dumps({
            "action": "finish",
            "tool_name": None,
            "reason": "partial_completion",
        }, ensure_ascii=False)))
        with patch(
            "app.agent.anomaly_agent.Main_RootCause_Agent.nodes.supervisor_reason.get_context_llm",
            return_value=fake_llm,
        ):
            result = await supervisor_reason(
                base_state(observations=observations),
                runtime(),
            )

        self.assertEqual(result["supervisor_decision"]["action"], "finish")
        self.assertIn("partial_completion", result["supervisor_decision"]["reason"])


if __name__ == "__main__":
    unittest.main()
