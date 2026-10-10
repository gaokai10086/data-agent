from datetime import date

from app.agent.anomaly_agent.request_parser import parse_explicit_month_request


def test_parse_explicit_month_anomaly_request():
    request = parse_explicit_month_request("为什么2025年7月销售额比6月下降了？")

    assert request.metric == "销售额"
    assert request.target_period is not None
    assert request.target_period.start_date == date(2025, 7, 1)
    assert request.target_period.end_date == date(2025, 7, 31)
    assert request.comparison is not None
    assert request.comparison.comparison_type == "mom"
    assert request.comparison.period is not None
    assert request.comparison.period.start_date == date(2025, 6, 1)
    assert request.comparison.period.end_date == date(2025, 6, 30)
