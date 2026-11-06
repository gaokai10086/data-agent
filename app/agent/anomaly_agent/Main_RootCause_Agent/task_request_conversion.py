"""TaskRequest -> AnalysisRequest 的确定性转换规则。

只在不补造任何条件的前提下完成转换，供变化检验 Tool 或后续 Supervisor 使用。
"""

from __future__ import annotations

from app.agent.anomaly_agent.schemas import (
    AnalysisRequest,
    FilterCondition,
    MonthPeriod,
    TaskRequest,
)


def normalize_filter(condition: FilterCondition) -> FilterCondition:
    """归一化明确的直辖市别名；不对任意地名盲目删除行政区后缀。"""
    municipality_aliases = {
        "北京": "北京", "北京市": "北京",
        "上海": "上海", "上海市": "上海",
        "天津": "天津", "天津市": "天津",
        "重庆": "重庆", "重庆市": "重庆",
    }
    value = condition.value.strip()
    if condition.dimension is None:
        return condition.model_copy(update={"dimension": None, "value": value})
    dimension = condition.dimension.strip()
    if dimension in {"地区", "地域", "城市", "市", "大区"} and value in municipality_aliases:
        return condition.model_copy(update={"dimension": "城市", "value": municipality_aliases[value]})
    return condition.model_copy(update={"dimension": dimension, "value": value})


def _period_text(period: MonthPeriod) -> str:
    return f"{period.year:04d}-{period.month:02d}"


def comparison_type_for_periods(
    target: MonthPeriod,
    comparison: MonthPeriod,
) -> str:
    """推导 mom / yoy / custom；两个周期相同视为非法。"""
    target_index = target.year * 12 + target.month
    comparison_index = comparison.year * 12 + comparison.month
    if target_index == comparison_index:
        raise ValueError("目标月份与对比月份不能相同")
    if comparison_index == target_index - 1:
        return "mom"
    if comparison.month == target.month and comparison.year == target.year - 1:
        return "yoy"
    return "custom"


def task_request_to_analysis_request(task: TaskRequest) -> AnalysisRequest:
    """仅在存在单个明确指标和两个合法比较周期时转换。"""
    if len(task.metrics) != 1:
        raise ValueError("仅支持将单个明确指标转换为变化检验请求")

    metric = task.metrics[0].strip()
    if not metric:
        raise ValueError("无法识别要分析的指标")
    if task.target_period is None or task.comparison_period is None:
        raise ValueError("缺少目标周期或对比周期，无法转换为变化检验请求")
    if task.relative_time:
        raise ValueError("存在相对时间表达，且没有明确的绝对周期，无法直接转换")

    target = task.target_period
    comparison = task.comparison_period
    comparison_type = comparison_type_for_periods(target, comparison)
    return AnalysisRequest(
        metric=metric,
        target_period=_period_text(target),
        comparison_period=_period_text(comparison),
        comparison_type=comparison_type,  # type: ignore[arg-type]
        filters=[normalize_filter(condition) for condition in task.filters],
        expected_direction=task.expected_direction,
    )
