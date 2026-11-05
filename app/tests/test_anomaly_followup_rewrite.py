"""异动分析追问改写的离线回归。"""

import unittest

from app.service.conversation_context import QueryContext, rewrite_query


REQUEST = {
    "metric": "销售额",
    "target_period": "2025-07",
    "comparison_period": "2025-06",
    "comparison_type": "mom",
    "expected_direction": "decrease",
    "filters": [{"dimension": "城市", "value": "上海", "operator": "="}],
}


def make_context(analysis_request: dict | None = REQUEST) -> QueryContext:
    return QueryContext(
        original_query="为什么2025年7月上海销售额比2025年6月下降了？",
        rewritten_query="为什么2025年7月上海销售额比2025年6月下降了？",
        intent="anomaly_analysis",
        analysis_request=analysis_request,
    )


class AnomalyFollowupRewriteTest(unittest.TestCase):
    def test_replace_city(self):
        rewritten, changed = rewrite_query("那北京呢", make_context())
        self.assertTrue(changed)
        self.assertIn("北京", rewritten)
        self.assertNotIn("上海", rewritten)
        self.assertIn("2025年7月", rewritten)
        self.assertIn("2025年6月", rewritten)
        self.assertIn("销售额", rewritten)

    def test_replace_channel(self):
        request = {
            **REQUEST,
            "filters": [
                {"dimension": "城市", "value": "上海", "operator": "="},
                {"dimension": "渠道", "value": "线下", "operator": "="},
            ],
        }
        rewritten, changed = rewrite_query("那线上呢", make_context(request))
        self.assertTrue(changed)
        self.assertIn("线上渠道", rewritten)
        self.assertNotIn("线下", rewritten)
        self.assertIn("上海", rewritten)

    def test_add_city_when_previous_has_none(self):
        request = {**REQUEST, "filters": []}
        rewritten, changed = rewrite_query("那北京呢", make_context(request))
        self.assertTrue(changed)
        self.assertIn("北京", rewritten)

    def test_non_followup_keeps_original(self):
        question = "为什么2025年8月销售额比7月下降了？"
        rewritten, changed = rewrite_query(question, make_context())
        self.assertFalse(changed)
        self.assertEqual(rewritten, question)

    def test_data_query_rewrite_still_works(self):
        previous = QueryContext(
            original_query="查询华东销售额",
            rewritten_query="查询华东最近30天销售额",
            intent="query_database",
            filters=("华东",),
        )
        rewritten, changed = rewrite_query("那华南呢", previous)
        self.assertTrue(changed)
        self.assertIn("华南", rewritten)
        self.assertNotIn("华东", rewritten)


if __name__ == "__main__":
    unittest.main()
