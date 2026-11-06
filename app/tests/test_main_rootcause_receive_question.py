"""Main_RootCause_Agent 主 Agent 的输入与 Skill 选择边界测试。"""

import unittest

from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.receive_question import receive_question
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.select_skill import select_skill
from app.agent.anomaly_agent.Main_RootCause_Agent.skills.registry import UnsupportedSkillError
from app.agent.anomaly_agent.schemas import AnalysisRequest


def build_request(metric: str = "销售额") -> AnalysisRequest:
    return AnalysisRequest(
        metric=metric,
        target_period="2025-07",
        comparison_period="2025-06",
        comparison_type="mom",
    )


class MainRootCauseNodesTest(unittest.TestCase):
    def test_receive_question_keeps_original_question(self):
        question = "  为什么销售额下降？  "
        self.assertEqual(receive_question({"raw_question": question}), {})

    def test_receive_question_rejects_blank_question(self):
        with self.assertRaisesRegex(ValueError, "问题不能为空"):
            receive_question({"raw_question": "  "})

    def test_select_skill_requires_parse_result(self):
        with self.assertRaisesRegex(ValueError, "完成问题解析"):
            select_skill({"raw_question": "x"})

    def test_select_skill_by_metric(self):
        result = select_skill({
            "analysis_request": build_request(),
        })
        self.assertEqual(result["selected_skill_name"], "sales_amount_anomaly")

    def test_select_skill_rejects_unsupported_metric(self):
        with self.assertRaises(UnsupportedSkillError):
            select_skill({
                "analysis_request": build_request("利润"),
            })


if __name__ == "__main__":
    unittest.main()
