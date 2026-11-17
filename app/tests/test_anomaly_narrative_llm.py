"""One-call narrative contract and fallback tests; no network access."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.runnables import RunnableLambda

from app.service.anomaly_narrative_llm import enrich_report


class NarrativeLLMTest(unittest.TestCase):
    def setUp(self):
        self.report = {
            "overall": {"metric": "销售额", "change_amount": "-50"},
            "overview_insight": "销售额下降 50。",
            "narrative_source": "fallback",
            "drilldown_sections": [{
                "task_id": "t1", "depth": 0, "dimension_name": "销售渠道",
                "selected_member": "线下旗舰店", "parent_scope": [],
                "rows": [{"成员": "线下旗舰店", "变化金额": "-50"}],
                "reconciliation": {"comparison_matches": True, "current_matches": True, "change_matches": True},
                "narrative_summary": "真实成员下降。",
            }],
        }

    def test_valid_output_preserves_facts_and_references(self):
        calls = []

        async def fake_llm(_):
            calls.append(1)
            return '{"overview":{"text":"销售额减少了 50，主要异常集中于已验证的渠道成员。","evidence_ids":["overall","path:t1"]},"decomposition":null,"drilldowns":[{"task_id":"t1","text":"该成员下降了 50，下一层继承当前范围。","evidence_ids":["path:t1"]}],"diagnosis":null}'

        with patch("app.service.anomaly_narrative_llm.get_context_llm", return_value=RunnableLambda(fake_llm)):
            result = asyncio.run(enrich_report(self.report, SimpleNamespace(context={})))
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["narrative_source"], "llm")
        self.assertEqual(result["narrative_evidence_ids"]["drilldowns"]["t1"], ["path:t1"])
        self.assertEqual(result["drilldown_sections"][0]["rows"], self.report["drilldown_sections"][0]["rows"])

    def test_invented_number_or_bad_reference_falls_back(self):
        async def fake_llm(_):
            return '{"overview":{"text":"下降了 99%。","evidence_ids":["fake"]},"decomposition":null,"drilldowns":[],"diagnosis":null}'

        with patch("app.service.anomaly_narrative_llm.get_context_llm", return_value=RunnableLambda(fake_llm)):
            result = asyncio.run(enrich_report(self.report, SimpleNamespace(context={})))
        self.assertEqual(result["narrative_source"], "fallback")
        self.assertEqual(result["overview_insight"], self.report["overview_insight"])

    def test_invented_number_with_valid_references_falls_back(self):
        async def fake_llm(_):
            return '{"overview":{"text":"销售额下降了 99。","evidence_ids":["overall","path:t1"]},"decomposition":null,"drilldowns":[{"task_id":"t1","text":"该渠道下降明显。","evidence_ids":["path:t1"]}],"diagnosis":null}'

        with patch("app.service.anomaly_narrative_llm.get_context_llm", return_value=RunnableLambda(fake_llm)):
            result = asyncio.run(enrich_report(self.report, SimpleNamespace(context={})))
        self.assertEqual(result["narrative_source"], "fallback")
        self.assertEqual(result["narrative_fallback_reason"], "ValueError")

    def test_network_failure_falls_back(self):
        with patch("app.service.anomaly_narrative_llm.get_context_llm", side_effect=ConnectionError("offline")):
            result = asyncio.run(enrich_report(self.report, SimpleNamespace(context={})))
        self.assertEqual(result["narrative_source"], "fallback")


if __name__ == "__main__":
    unittest.main()
