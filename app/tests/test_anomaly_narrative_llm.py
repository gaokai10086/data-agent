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

    def test_invented_member_falls_back(self):
        async def fake_llm(_):
            return '{"overview":{"text":"主要下降成员是“虚构门店”，减少 50。","evidence_ids":["overall","path:t1"]},"decomposition":null,"drilldowns":[{"task_id":"t1","text":"“虚构门店”减少 50。","evidence_ids":["path:t1"]}],"diagnosis":null}'
        with patch("app.service.anomaly_narrative_llm.get_context_llm", return_value=RunnableLambda(fake_llm)):
            result = asyncio.run(enrich_report(self.report, SimpleNamespace(context={})))
        self.assertEqual(result["narrative_source"], "fallback")

    def test_real_member_and_own_amount_are_accepted(self):
        async def fake_llm(_):
            return '{"overview":{"text":"销售额下降 50，渠道“线下旗舰店”是重点范围。","evidence_ids":["overall","path:t1"]},"decomposition":null,"drilldowns":[{"task_id":"t1","text":"成员“线下旗舰店”减少 50。","evidence_ids":["path:t1"]}],"diagnosis":null}'
        with patch("app.service.anomaly_narrative_llm.get_context_llm", return_value=RunnableLambda(fake_llm)):
            result = asyncio.run(enrich_report(self.report, SimpleNamespace(context={})))
        self.assertEqual(result["narrative_source"], "llm")

    def test_unquoted_invented_store_falls_back(self):
        async def fake_llm(_):
            return '{"overview":{"text":"销售额下降 50，虚构门店减少 50。","evidence_ids":["overall","path:t1"]},"decomposition":null,"drilldowns":[{"task_id":"t1","text":"虚构门店减少 50。","evidence_ids":["path:t1"]}],"diagnosis":null}'
        with patch("app.service.anomaly_narrative_llm.get_context_llm", return_value=RunnableLambda(fake_llm)):
            result = asyncio.run(enrich_report(self.report, SimpleNamespace(context={})))
        self.assertEqual(result["narrative_source"], "fallback")

    def test_member_cannot_borrow_amount_from_another_path(self):
        report = {**self.report, "drilldown_sections": [*self.report["drilldown_sections"], {
            "task_id": "t2", "depth": 1, "dimension_name": "地区", "selected_member": "华东", "parent_scope": [],
            "rows": [{"成员": "华东", "变化金额": "-30"}],
            "reconciliation": {"comparison_matches": True, "current_matches": True, "change_matches": True},
        }]}
        async def fake_llm(_):
            return '{"overview":{"text":"销售额下降 50，最终落在华东。","evidence_ids":["overall","path:t1","path:t2"]},"decomposition":null,"drilldowns":[{"task_id":"t1","text":"线下旗舰店减少 30。","evidence_ids":["path:t1","path:t2"]},{"task_id":"t2","text":"华东减少 30。","evidence_ids":["path:t2"]}],"diagnosis":null}'
        with patch("app.service.anomaly_narrative_llm.get_context_llm", return_value=RunnableLambda(fake_llm)):
            result = asyncio.run(enrich_report(report, SimpleNamespace(context={})))
        self.assertEqual(result["narrative_source"], "fallback")

    def test_member_decrease_cannot_borrow_its_comparison_value(self):
        report = {**self.report, "drilldown_sections": [{**self.report["drilldown_sections"][0],
            "rows": [{"成员": "线下旗舰店", "对比值": "30", "变化金额": "-50"}]}]}
        async def fake_llm(_):
            return '{"overview":{"text":"销售额下降 50，渠道“线下旗舰店”是重点范围。","evidence_ids":["overall","path:t1"]},"decomposition":null,"drilldowns":[{"task_id":"t1","text":"线下旗舰店减少 30。","evidence_ids":["path:t1"]}],"diagnosis":null}'
        with patch("app.service.anomaly_narrative_llm.get_context_llm", return_value=RunnableLambda(fake_llm)):
            result = asyncio.run(enrich_report(report, SimpleNamespace(context={})))
        self.assertEqual(result["narrative_source"], "fallback")

    def test_this_member_cannot_borrow_other_path_amount(self):
        report = {**self.report, "drilldown_sections": [*self.report["drilldown_sections"], {
            "task_id": "t2", "depth": 1, "dimension_name": "地区", "selected_member": "华东", "parent_scope": [],
            "rows": [{"成员": "华东", "变化金额": "-30"}],
            "reconciliation": {"comparison_matches": True, "current_matches": True, "change_matches": True},
        }]}
        async def fake_llm(_):
            return '{"overview":{"text":"销售额下降 50，最终落在华东。","evidence_ids":["overall","path:t1","path:t2"]},"decomposition":null,"drilldowns":[{"task_id":"t1","text":"该成员减少 30。","evidence_ids":["path:t1","path:t2"]},{"task_id":"t2","text":"华东减少 30。","evidence_ids":["path:t2"]}],"diagnosis":null}'
        with patch("app.service.anomaly_narrative_llm.get_context_llm", return_value=RunnableLambda(fake_llm)):
            result = asyncio.run(enrich_report(report, SimpleNamespace(context={})))
        self.assertEqual(result["narrative_source"], "fallback")

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

    def test_period_percent_and_wan_conversion_are_accepted(self):
        report = dict(self.report)
        report["overall"] = {
            "metric": "销售额", "comparison_period": "2026-07", "target_period": "2026-08",
            "change_amount": "-658333", "change_rate": "-0.6647",
        }

        async def fake_llm(_):
            return '{"overview":{"text":"2026年8月销售额较2026年7月下降约65.83万元，降幅66.47%，主要异常集中于已验证渠道。","evidence_ids":["overall","path:t1"]},"decomposition":null,"drilldowns":[{"task_id":"t1","text":"该成员下降了50，后续范围沿此成员收窄。","evidence_ids":["path:t1"]}],"diagnosis":null}'

        with patch("app.service.anomaly_narrative_llm.get_context_llm", return_value=RunnableLambda(fake_llm)):
            result = asyncio.run(enrich_report(report, SimpleNamespace(context={})))
        self.assertEqual(result["narrative_source"], "llm")


if __name__ == "__main__":
    unittest.main()
