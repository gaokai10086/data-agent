"""Main_RootCause_Agent 的输入与 Skill 选择边界测试。"""

import unittest

from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.receive_question import receive_question
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.select_skill import select_skill
from app.agent.anomaly_agent.Main_RootCause_Agent.skills.registry import UnsupportedSkillError
from app.agent.anomaly_agent.schemas import TaskRequest


def build_task(metrics: list[str] | None = None) -> TaskRequest:
    return TaskRequest(
        objective="分析销售额变化",
        metrics=metrics if metrics is not None else ["销售额"],
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
        result = select_skill({"task_request": build_task()})
        self.assertEqual(result["selected_skill_name"], "sales_amount_anomaly")

    def test_select_skill_rejects_unsupported_metric(self):
        with self.assertRaises(UnsupportedSkillError):
            select_skill({"task_request": build_task(["利润"])})

    def test_select_skill_rejects_multiple_metrics(self):
        with self.assertRaisesRegex(ValueError, "多个指标"):
            select_skill({"task_request": build_task(["销售额", "利润"])})


if __name__ == "__main__":
    unittest.main()
