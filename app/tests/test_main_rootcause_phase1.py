"""Main_RootCause_Agent 第一阶段统一任务理解最小链路测试。"""

from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from pydantic import ValidationError

from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.parse_task import parse_task
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.receive_question import receive_question
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.select_skill import select_skill
from app.agent.anomaly_agent.Main_RootCause_Agent.skills.registry import UnsupportedSkillError
from app.agent.anomaly_agent.Main_RootCause_Agent.state import RootCauseState
from app.agent.anomaly_agent.schemas import MonthPeriod, TaskRequest


class MainRootCausePhase1Test(unittest.TestCase):
    def test_state_schema_contains_only_phase1_fields(self):
        self.assertEqual(
            set(RootCauseState.__annotations__),
            {"raw_question", "task_request", "selected_skill_name"},
        )

    def test_valid_question_produces_task_request_and_skill(self):
        question = "为什么2025年7月上海销售额比2025年6月下降了？"
        model_reply = AIMessage(content=json.dumps({
            "objective": "分析上海销售额环比下降",
            "metrics": ["销售额"],
            "target_period": {"year": 2025, "month": 7},
            "comparison_period": {"year": 2025, "month": 6},
            "relative_time": None,
            "filters": [{"dimension": "城市", "value": "上海", "operator": "="}],
            "dimensions": ["城市"],
            "analysis_goals": ["compare", "attribute_dimension"],
            "expected_direction": "decrease",
            "hypothesis": None,
            "missing_info": [],
        }, ensure_ascii=False))
        fake_llm = RunnableLambda(lambda _: model_reply)
        runtime = SimpleNamespace(context={}, stream_writer=lambda _: None)

        with patch(
            "app.agent.anomaly_agent.Main_RootCause_Agent.nodes.parse_task.get_context_llm",
            return_value=fake_llm,
        ):
            parsed = asyncio.run(parse_task({"raw_question": question}, runtime))

        task = parsed["task_request"]
        self.assertEqual(task.metrics, ["销售额"])
        self.assertEqual(task.target_period, MonthPeriod(year=2025, month=7))

        selected = select_skill({"task_request": task})
        self.assertEqual(selected["selected_skill_name"], "sales_amount_anomaly")

    def test_blank_question_fails(self):
        with self.assertRaisesRegex(ValueError, "问题不能为空"):
            receive_question({"raw_question": "  "})

    def test_invalid_month_fails(self):
        with self.assertRaises(ValidationError):
            TaskRequest(
                objective="分析",
                metrics=["销售额"],
                target_period={"year": 2025, "month": 13},
            )

    def test_unsupported_metric_fails(self):
        task = TaskRequest(objective="分析利润", metrics=["利润"])
        with self.assertRaises(UnsupportedSkillError):
            select_skill({"task_request": task})

    def test_application_entry_imports_without_error(self):
        from app.agent.anomaly_agent.Main_RootCause_Agent.graph import main_root_cause_agent
        from app.agent.orchestrator.nodes.run_anomaly_agent import run_anomaly_agent

        self.assertTrue(callable(main_root_cause_agent.ainvoke))
        self.assertTrue(callable(run_anomaly_agent))


if __name__ == "__main__":
    unittest.main()
