"""总体指标对比处理器。"""

from __future__ import annotations

import asyncio
from decimal import Decimal, InvalidOperation
from typing import Any

from langgraph.runtime import Runtime

from app.agent.anomaly_agent.Change_Inspection_Agent.tools.base import BaseAnomalyHandler
from app.agent.anomaly_agent.Change_Inspection_Agent.state import PreparationState
from app.agent.anomaly_agent.common.data_query import DataQueryRequest, DataQueryTool
from app.agent.anomaly_agent.schemas import (
    AnalysisRequest,
    MetricContext,
    OverallComparisonResult,
)
from app.agent.data_agent.context import DataAgentContext
from app.core.logging import logger


def build_period_query(
    request: AnalysisRequest,
    metric_context: MetricContext,
    period: str,
    role: str,
) -> str:
    """为一个周期生成标准查询语句；两个周期使用同一模板。"""

    ungrounded = [item.value for item in request.filters if item.dimension is None]
    if ungrounded:
        raise ValueError(
            f"以下筛选条件尚未完成值召回 grounding，无法生成查询：{', '.join(ungrounded)}"
        )

    filter_text = ""
    if request.filters:
        filter_text = "，筛选条件为" + "、".join(
            f"{item.dimension}{item.operator}{item.value}" for item in request.filters
        )

    return (
        f"查询{metric_context.name}在{period}的总体值，不按维度分组"
        f"{filter_text}；这是{role}周期查询，只返回一行，"
        "列名使用 value。"
    )


def _to_decimal(value: Any, field_name: str) -> Decimal | None:
    """把数据库常见数值类型归一化为 Decimal。"""

    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_name} 必须是数值，实际值为：{value!r}")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{field_name} 必须是数值，实际值为：{value!r}") from exc
    if not number.is_finite():
        raise ValueError(f"{field_name} 必须是有限数值，实际值为：{value!r}")
    return number


def extract_single_value(rows: Any, period: str) -> Decimal | None:
    """校验并读取单周期查询协议中的一个数值。"""

    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError(f"周期 {period} 查询应返回一行结构化结果，实际结果为：{rows!r}")

    row = rows[0]
    if "value" not in row:
        raise ValueError(
            f"周期 {period} 查询结果缺少 value 字段；实际字段为：{list(row)}"
        )
    return _to_decimal(row["value"], f"{period}.value")


def calculate_change(
    current_value: Decimal | None,
    comparison_value: Decimal | None,
) -> tuple[Decimal | None, Decimal | None]:
    """用确定性代码计算变化量和变化率，不交给 LLM。"""

    if current_value is None or comparison_value is None:
        return None, None

    change_amount = current_value - comparison_value
    if comparison_value == 0:
        return change_amount, None
    return change_amount, change_amount / comparison_value


class OverallComparisonHandler(BaseAnomalyHandler):
    """通过公共 DataQueryTool 生成总体指标对比结果。"""

    analysis_type = "overall_comparison"
    data_query_tool = DataQueryTool()

    async def run(
        self,
        state: PreparationState,
        runtime: Runtime[DataAgentContext],
    ) -> OverallComparisonResult:
        request: AnalysisRequest | None = state.get("analysis_request")
        metric_context: MetricContext | None = state.get("metric_context")
        if request is None or metric_context is None:
            raise ValueError("总体查询前必须先完成请求解析和指标上下文加载")

        return await self.compare(request, metric_context, runtime)

    async def compare(
        self,
        request: AnalysisRequest,
        metric_context: MetricContext,
        runtime: Runtime[DataAgentContext],
    ) -> OverallComparisonResult:
        """按请求和指标上下文执行两个周期的总体对比。"""

        async def query_period(period: str, role: str) -> Decimal | None:
            logger.info("开始周期查询")
            try:
                value = await execute_period_query(period, role)
            except Exception as exc:
                logger.error("周期查询失败：{}", exc)
                raise
            logger.info("周期查询完成：{}", value)
            return value

        async def execute_period_query(period: str, role: str) -> Decimal | None:
            query = build_period_query(request, metric_context, period, role)
            result = await self.data_query_tool.run(
                DataQueryRequest(
                    query=query,
                    request_id=request.request_id,
                    purpose="anomaly_overall_comparison",
                    role=role,
                    period=period,
                    metric_key=metric_context.metric_key,
                    expected_columns=["value"],
                    max_rows=1,
                ),
                runtime,
            )
            if not result.success:
                raise RuntimeError(
                    f"Data Agent 查询失败（周期 {period}）：{result.error_message}"
                )
            return extract_single_value(result.rows, period)

        # 两个周期互相独立，必须并发调用 Data Agent，避免把两次查询延迟串联。
        current_value, comparison_value = await asyncio.gather(
            query_period(request.target_period, "目标"),
            query_period(request.comparison_period, "对比"),
        )
        change_amount, change_rate = calculate_change(current_value, comparison_value)
        return OverallComparisonResult(
            request_id=request.request_id,
            metric_key=metric_context.metric_key,
            target_period=request.target_period,
            comparison_period=request.comparison_period,
            current_value=current_value,
            comparison_value=comparison_value,
            change_amount=change_amount,
            change_rate=change_rate,
        )
