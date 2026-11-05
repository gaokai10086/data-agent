"""异动问题解析：指标与筛选条件提取的回归测试。

分两层：
- 确定性校验：不依赖网络，验证 ``validate_extracted_request`` 与
  ``normalize_filter`` 对指标、多筛选、周期和错误输入的处理。
- 真实模型抽取：验证 LLM 能否从自然语言准确提出指标和筛选数组，
  需要网络访问，默认跳过；设置 ``RUN_ANOMALY_LLM_TESTS=1`` 后运行。
"""
from __future__ import annotations

import asyncio
import os
import unittest
from types import SimpleNamespace

from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.parse_request import (
    normalize_filter,
    parse_request,
    validate_extracted_request,
)
from app.agent.anomaly_agent.schemas import ExtractedRequest, FilterCondition


class MetricExtractionValidationTest(unittest.TestCase):
    """离线确定性校验：不调用模型。"""

    def test_metric_name_is_preserved(self):
        request = validate_extracted_request(ExtractedRequest(
            metric="销售额", target_year=2025, target_month=7, comparison_month=6,
        ))
        self.assertEqual(request.metric, "销售额")

    def test_multiple_filters_are_kept_in_order(self):
        request = validate_extracted_request(ExtractedRequest(
            metric="销售额", target_year=2025, target_month=7, comparison_month=6,
            filters=[
                FilterCondition(dimension="城市", value="上海"),
                FilterCondition(dimension="渠道", value="线上"),
            ],
        ))
        self.assertEqual(len(request.filters), 2)
        self.assertEqual(
            [(f.dimension, f.value) for f in request.filters],
            [("城市", "上海"), ("渠道", "线上")],
        )

    def test_filters_is_an_array_not_a_single_field(self):
        # 无筛选时是空数组，而不是某个绑定死的默认筛选。
        request = validate_extracted_request(ExtractedRequest(
            metric="销售额", target_year=2025, target_month=7, comparison_month=6,
        ))
        self.assertEqual(request.filters, [])

    def test_municipality_alias_normalized_per_filter(self):
        normalized = [
            normalize_filter(FilterCondition(dimension="地区", value="北京市")),
            normalize_filter(FilterCondition(dimension="大区", value="华北")),
        ]
        self.assertEqual(
            [(f.dimension, f.value) for f in normalized],
            [("城市", "北京"), ("大区", "华北")],
        )

    def test_bare_entity_keeps_none_dimension(self):
        request = validate_extracted_request(ExtractedRequest(
            metric="销售额", target_year=2025, target_month=7, comparison_month=6,
            filters=[
                FilterCondition(dimension="城市", value="北京"),
                FilterCondition(value="凯乐石"),
            ],
        ))
        self.assertEqual(
            [(f.dimension, f.value) for f in request.filters],
            [("城市", "北京"), (None, "凯乐石")],
        )

    def test_mom_and_yoy_are_computed(self):
        mom = validate_extracted_request(ExtractedRequest(
            metric="销售额", target_year=2025, target_month=7, comparison_month=6,
        ))
        self.assertEqual((mom.comparison_type, mom.comparison_period), ("mom", "2025-06"))
        yoy = validate_extracted_request(ExtractedRequest(
            metric="销售额", target_year=2025, target_month=7,
            comparison_year=2024, comparison_month=7,
        ))
        self.assertEqual((yoy.comparison_type, yoy.comparison_period), ("yoy", "2024-07"))

    def test_same_month_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_extracted_request(ExtractedRequest(
                metric="销售额", target_year=2025, target_month=7, comparison_month=7,
            ))

    def test_blank_metric_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_extracted_request(ExtractedRequest(
                metric=" ", target_year=2025, target_month=7, comparison_month=6,
            ))


def _parse(question: str) -> dict:
    runtime = SimpleNamespace(context={}, stream_writer=lambda _: None)
    return asyncio.run(parse_request({"raw_question": question}, runtime))


@unittest.skipUnless(
    os.getenv("RUN_ANOMALY_LLM_TESTS") == "1",
    "需要网络访问模型，设置 RUN_ANOMALY_LLM_TESTS=1 后运行",
)
class MetricExtractionLLMTest(unittest.TestCase):
    """真实模型抽取质量回归，默认跳过。"""

    def test_extracts_metric_and_periods(self):
        request = _parse("为什么2025年7月上海销售额比2025年6月下降了？")["analysis_request"]
        self.assertEqual(request.metric, "销售额")
        self.assertEqual(request.target_period, "2025-07")
        self.assertEqual(request.comparison_period, "2025-06")
        self.assertEqual(request.comparison_type, "mom")

    def test_multiple_filters_extracted_as_array(self):
        request = _parse("2025年7月上海线上渠道销售额比6月下降了多少？")["analysis_request"]
        values = {(f.dimension, f.value) for f in request.filters}
        self.assertEqual(len(values), 2)
        self.assertIn(("城市", "上海"), values)
        self.assertIn(("渠道", "线上"), values)

    def test_bare_entity_dimension_stays_null(self):
        request = _parse("为什么北京地区凯乐石2025年7月的销售额比2025年6月下降了？")["analysis_request"]
        pairs = {(f.dimension, f.value) for f in request.filters}
        self.assertIn(("城市", "北京"), pairs)
        self.assertIn((None, "凯乐石"), pairs)


if __name__ == "__main__":
    unittest.main()
