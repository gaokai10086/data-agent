"""Main_RootCause_Agent 第一阶段最小链路测试。

这些测试覆盖三个业务要求：
1. 有效问题可以解析出 AnalysisRequest 并选中 Skill；
2. 空问题、无效月份和不支持的指标会正确失败；
3. 应用层新入口不存在导入错误。
"""

from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.parse_request import (
    parse_request,
    validate_extracted_request,
)
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.receive_question import receive_question
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.select_skill import select_skill
from app.agent.anomaly_agent.Main_RootCause_Agent.skills.registry import UnsupportedSkillError
from app.agent.anomaly_agent.Main_RootCause_Agent.state import RootCauseState
from app.agent.anomaly_agent.schemas import ExtractedRequest


class MainRootCausePhase1Test(unittest.TestCase):
    def test_state_schema_contains_only_phase1_fields(self):
        self.assertEqual(
            set(RootCauseState.__annotations__),
            {"raw_question", "analysis_request", "selected_skill_name"},
        )

    def test_valid_question_produces_analysis_request_and_skill(self):
        question = "为什么2025年7月上海销售额比2025年6月下降了？"
        model_reply = AIMessage(content=json.dumps({
            "metric": "销售额",
            "target_year": 2025,
            "target_month": 7,
            "comparison_year": None,
            "comparison_month": 6,
            "filters": [],
            "expected_direction": "decrease",
        }, ensure_ascii=False))
        fake_llm = RunnableLambda(lambda _: model_reply)
        runtime = SimpleNamespace(context={}, stream_writer=lambda _: None)

        with patch(
            "app.agent.anomaly_agent.Main_RootCause_Agent.nodes.parse_request.get_context_llm",
            return_value=fake_llm,
        ):
            parsed = asyncio.run(parse_request({"raw_question": question}, runtime))

        request = parsed["analysis_request"]
        self.assertEqual(request.metric, "销售额")
        self.assertEqual(request.target_period, "2025-07")
        self.assertEqual(request.comparison_period, "2025-06")

        selected = select_skill({"analysis_request": request})
        self.assertEqual(selected["selected_skill_name"], "sales_amount_anomaly")

    def test_blank_question_fails(self):
        with self.assertRaisesRegex(ValueError, "问题不能为空"):
            receive_question({"raw_question": "  "})

    def test_invalid_month_fails(self):
        with self.assertRaisesRegex(ValueError, "无效月份"):
            validate_extracted_request(ExtractedRequest(
                metric="销售额",
                target_year=2025,
                target_month=13,
                comparison_month=6,
            ))

    def test_unsupported_metric_fails(self):
        request = validate_extracted_request(ExtractedRequest(
            metric="利润",
            target_year=2025,
            target_month=7,
            comparison_month=6,
        ))
        with self.assertRaises(UnsupportedSkillError):
            select_skill({"analysis_request": request})

    def test_application_entry_imports_without_error(self):
        from app.agent.anomaly_agent.Main_RootCause_Agent.graph import main_root_cause_agent
        from app.agent.orchestrator.nodes.run_anomaly_agent import run_anomaly_agent

        self.assertTrue(callable(main_root_cause_agent.ainvoke))
        self.assertTrue(callable(run_anomaly_agent))


if __name__ == "__main__":
    unittest.main()
