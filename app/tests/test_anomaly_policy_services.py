"""异动分析策略和服务层的无数据库回归测试。"""
from __future__ import annotations

from app.agent.anomaly_agent.policies.sales_amount_policy import default_sales_context
from app.agent.anomaly_agent.request_parser import parse_explicit_month_request
from app.agent.anomaly_agent.services.request_builder_service import (
    build_requests,
    build_scoped_breakdown_requests,
)


def test_sales_policy_builds_initial_and_scoped_requests():
    request = parse_explicit_month_request("为什么2025年7月销售额比6月下降了？")
    context = default_sales_context()

    initial = build_requests(request, context)
    assert [item.request_id for item in initial] == [
        "overall_sales_amount",
        "region_sales_amount",
    ]

    scoped = build_scoped_breakdown_requests(request, context, "华西")
    assert [item.dimensions for item in scoped] == [["渠道"], ["门店"], ["促销活动"]]
    assert all(item.filters[0].value == "华西" for item in scoped)

