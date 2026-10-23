"""核实已返回数值的变化方向和请求对应关系，不查库、不调用 LLM、不检测统计异常。"""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from langgraph.runtime import Runtime

from app.agent.anomaly_agent.schemas import (
    AnalysisRequest, BaseAnomalyResult, ChangeVerification, MetricContext, OverallComparisonResult,
)
from app.agent.anomaly_agent.state import AnomalyState
from app.agent.data_agent.context import DataAgentContext


def _today() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def _period_start(period: str) -> date:
    if not re.fullmatch(r"\d{4}-\d{2}", period):
        raise ValueError("比较周期必须是 YYYY-MM")
    return date.fromisoformat(period + "-01")


def _number(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def assess_change(
    request: AnalysisRequest | None,
    metric: MetricContext | None,
    result: BaseAnomalyResult | None,
    *,
    as_of_date: date | None = None,
) -> ChangeVerification:
    """纯函数：按月度请求核实结果，便于用固定日期和测试数据验证。

    日历已结束不代表 ETL 已完整；当前协议未提供查询口径指纹或数据水位，
    因而不宣称已经验证 SQL 的真实周期、筛选口径及数据完整性。
    """
    expected = request.expected_direction if request else "unspecified"

    def stop(reason, summary, actual="unknown"):
        return ChangeVerification(decision="stop", reason=reason, expected_direction=expected,
                                  actual_direction=actual, summary=summary)

    if request is None or metric is None or result is None:
        return stop("insufficient_data", "请求、指标定义或总体查询结果缺失，无法核实变化。")
    if not isinstance(result, OverallComparisonResult):
        return stop("unsupported_result", "当前变化核实只支持两个周期的总体指标对比结果。")
    if (result.request_id != request.request_id or result.metric_key != metric.metric_key
            or result.target_period != request.target_period
            or result.comparison_period != request.comparison_period):
        return stop("result_mismatch", "查询结果的请求标识、指标或比较周期与当前请求不一致，停止分析。")

    try:
        target = _period_start(request.target_period)
        comparison = _period_start(request.comparison_period)
    except ValueError:
        return stop("invalid_period", "比较周期格式无效，无法核实变化。")
    if target == comparison:
        return stop("invalid_period", "目标周期和对比周期相同，不能进行周期变化分析。")
    today = as_of_date or _today()
    current_month = today.replace(day=1)
    if target >= current_month or comparison >= current_month:
        return stop("incomplete_period", "比较中包含尚未结束或未来的月份，不能按完整月份直接比较；请指定已结束月份或对齐累计日期。")

    current_value, comparison_value = result.current_value, result.comparison_value
    if any(value is None or not value.is_finite() for value in (current_value, comparison_value)):
        return stop("insufficient_data", "至少一个周期的指标值缺失或不可用，不能把缺失值当作零进行变化判断。")
    actual = "decrease" if current_value < comparison_value else "increase" if current_value > comparison_value else "unchanged"
    if actual == "unchanged":
        return stop("no_change", f"{metric.name}两个周期的值均为{_number(current_value)}，未发生总体变化，不继续变化归因。", actual)
    direction_text = "下降" if actual == "decrease" else "上升"
    if expected != "unspecified" and actual != expected:
        expected_text = "下降" if expected == "decrease" else "上升"
        return stop("direction_mismatch", f"查询结果显示{metric.name}从{_number(comparison_value)}变为{_number(current_value)}，实际{direction_text}，不支持问题中“{expected_text}”的前提。", actual)

    # 差额由总体查询 Handler 计算，本节点只消费已有结果。
    if result.change_amount is None or not result.change_amount.is_finite():
        return stop("insufficient_data", "总体查询结果缺少可用变化量，无法生成完整的变化核实结论。", actual)
    if comparison_value + result.change_amount != current_value:
        return stop("result_mismatch", "已有变化量与两个周期的指标值不一致，停止分析并检查总体查询结果。", actual)
    warnings = []
    summary = f"{metric.name}从{_number(comparison_value)}变为{_number(current_value)}，{direction_text}{_number(abs(result.change_amount))}"
    if comparison_value > 0 and result.change_rate is not None and result.change_rate.is_finite():
        percent = (abs(result.change_rate) * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        summary += f"，幅度约{percent}%"
    elif comparison_value == 0:
        warnings.append("对比周期值为0，无法按常规公式计算变化率；可以根据数值和变化量继续分析。")
    elif comparison_value < 0:
        warnings.append("对比周期值为负数，变化方向按原始数值判断，避免按常规百分比误解方向。")
    else:
        warnings.append("变化率不可用，仅根据指标值和变化量核实方向。")
    summary += "。变化方向得到支持，可以继续分析变化来源；是否属于历史异常尚未评估。"
    return ChangeVerification(decision="continue", reason="change_confirmed", expected_direction=expected,
                              actual_direction=actual, summary=summary, warnings=warnings)


def verify_change(state: AnomalyState, runtime: Runtime[DataAgentContext]) -> dict[str, object]:
    """LangGraph 适配层：读取状态，把核实决策写回一个状态字段。"""
    return {"change_verification": assess_change(
        state.get("analysis_request"), state.get("metric_context"), state.get("analysis_result"),
    )}
