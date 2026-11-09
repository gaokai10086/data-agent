"""Main_RootCause_Agent Skill 入口阶段最小链路测试。"""

from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.extract_metrics import extract_metrics
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.load_skill import load_skill_node
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.receive_question import receive_question
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.select_skill import select_skill
from app.agent.anomaly_agent.Main_RootCause_Agent.state import RootCauseState


class MainRootCausePhase1Test(unittest.TestCase):
    def test_state_schema_contains_skill_entry_fields(self):
        self.assertEqual(
            set(RootCauseState.__annotations__),
            {
                "raw_question",
                "extracted_metrics",
                "selected_skill_name",
                "skill_match_status",
                "skill_match_reason",
            },
        )

    def test_metric_extraction_and_skill_matching(self):
        question = "为什么2025年7月销售额比6月下降？"
        model_reply = AIMessage(content=json.dumps({"metrics": ["销售额"]}, ensure_ascii=False))
        fake_llm = RunnableLambda(lambda _: model_reply)
        runtime = SimpleNamespace(context={}, stream_writer=lambda _: None)

        with patch(
            "app.agent.anomaly_agent.Main_RootCause_Agent.nodes.extract_metrics.get_context_llm",
            return_value=fake_llm,
        ):
            extracted = asyncio.run(extract_metrics({"raw_question": question}, runtime))

        self.assertEqual(extracted["extracted_metrics"], ["销售额"])
        selected = select_skill({
            "raw_question": question,
            "extracted_metrics": extracted["extracted_metrics"],
        })
        self.assertEqual(selected["skill_match_status"], "matched")
        self.assertEqual(selected["selected_skill_name"], "sales_amount_anomaly")
        load_skill_node({
            "selected_skill_name": selected["selected_skill_name"],
            "skill_match_reason": selected["skill_match_reason"],
        })

    def test_blank_question_fails(self):
        with self.assertRaisesRegex(ValueError, "问题不能为空"):
            receive_question({"raw_question": "  "})

    def test_application_entry_imports_without_error(self):
        from app.agent.anomaly_agent.Main_RootCause_Agent.graph import main_root_cause_agent
        from app.agent.orchestrator.nodes.run_anomaly_agent import run_anomaly_agent

        self.assertTrue(callable(main_root_cause_agent.ainvoke))
        self.assertTrue(callable(run_anomaly_agent))


if __name__ == "__main__":
    unittest.main()
