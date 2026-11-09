"""Main_RootCause_Agent 的输入与 Skill 选择边界测试。"""

import unittest

from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.receive_question import receive_question
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.select_skill import select_skill


def build_state(metrics: list[str]) -> dict[str, object]:
    return {"raw_question": "分析销售额变化", "extracted_metrics": metrics}


class MainRootCauseNodesTest(unittest.TestCase):
    def test_receive_question_keeps_original_question(self):
        question = "  为什么销售额下降？  "
        self.assertEqual(receive_question({"raw_question": question}), {})

    def test_receive_question_rejects_blank_question(self):
        with self.assertRaisesRegex(ValueError, "问题不能为空"):
            receive_question({"raw_question": "  "})

    def test_select_skill_by_metric(self):
        result = select_skill(build_state(["销售额"]))
        self.assertEqual(result["skill_result"]["status"], "matched")
        self.assertEqual(result["skill_result"]["skill_name"], "sales_amount_anomaly")

    def test_select_skill_rejects_unsupported_metric(self):
        result = select_skill(build_state(["利润"]))
        self.assertEqual(result["skill_result"]["status"], "unsupported")

    def test_select_skill_rejects_multiple_metrics(self):
        result = select_skill(build_state(["销售额", "利润"]))
        self.assertEqual(result["skill_result"]["status"], "unsupported")


if __name__ == "__main__":
    unittest.main()
