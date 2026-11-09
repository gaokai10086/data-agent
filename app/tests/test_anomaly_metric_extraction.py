"""TaskRequest -> AnalysisRequest 的确定性规则回归测试。"""

from __future__ import annotations

import unittest

from app.agent.anomaly_agent.Main_RootCause_Agent.parsing import (
    normalize_filter,
    task_request_to_analysis_request,
)
from app.agent.anomaly_agent.schemas import FilterCondition, MonthPeriod, TaskRequest


def task(**kwargs) -> TaskRequest:
    defaults = {
        "objective": "分析销售额变化",
        "metrics": ["销售额"],
        "target_period": MonthPeriod(year=2025, month=7),
        "comparison_period": MonthPeriod(year=2025, month=6),
        "expected_direction": "decrease",
    }
    defaults.update(kwargs)
    return TaskRequest(**defaults)


class TaskRequestConversionTest(unittest.TestCase):
    def test_mom_is_computed(self):
        request = task_request_to_analysis_request(task())
        self.assertEqual((request.comparison_type, request.comparison_period), ("mom", "2025-06"))

    def test_yoy_is_computed(self):
        request = task_request_to_analysis_request(task(
            target_period=MonthPeriod(year=2025, month=7),
            comparison_period=MonthPeriod(year=2024, month=7),
        ))
        self.assertEqual((request.comparison_type, request.comparison_period), ("yoy", "2024-07"))

    def test_custom_is_computed(self):
        request = task_request_to_analysis_request(task(
            target_period=MonthPeriod(year=2025, month=8),
            comparison_period=MonthPeriod(year=2025, month=5),
        ))
        self.assertEqual((request.comparison_type, request.comparison_period), ("custom", "2025-05"))

    def test_cross_year_mom_is_computed(self):
        request = task_request_to_analysis_request(task(
            target_period=MonthPeriod(year=2025, month=1),
            comparison_period=MonthPeriod(year=2024, month=12),
        ))
        self.assertEqual(request.comparison_type, "mom")
        self.assertEqual(request.comparison_period, "2024-12")

    def test_filters_are_preserved_and_normalized(self):
        request = task_request_to_analysis_request(task(filters=[
            FilterCondition(dimension="地区", value="北京市"),
            FilterCondition(value="凯乐石"),
        ]))
        self.assertEqual(
            [(f.dimension, f.value) for f in request.filters],
            [("城市", "北京"), (None, "凯乐石")],
        )

    def test_same_period_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "不能相同"):
            task_request_to_analysis_request(task(
                target_period=MonthPeriod(year=2025, month=7),
                comparison_period=MonthPeriod(year=2025, month=7),
            ))

    def test_missing_periods_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "缺少目标周期或对比周期"):
            task_request_to_analysis_request(task(
                target_period=None,
                comparison_period=None,
            ))

    def test_multiple_metrics_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "单个明确指标"):
            task_request_to_analysis_request(task(metrics=["销售额", "利润"]))

    def test_blank_metric_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "指标"):
            task_request_to_analysis_request(task(metrics=[" "]))


if __name__ == "__main__":
    unittest.main()
