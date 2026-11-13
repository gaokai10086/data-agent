"""真实依赖指标取数和确定性乘法拆解计算。"""

from __future__ import annotations

import asyncio
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Any

from app.agent.anomaly_agent.MetricDecomposition_Agent.schemas import (
    DecompositionComponent,
    DecompositionReconciliation,
    FactorPeriodValue,
)
from app.agent.anomaly_agent.common.data_query import DataQueryRequest, DataQueryTool


class DecompositionQueryError(RuntimeError):
    pass


def _decimal(value: Any, label: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise DecompositionQueryError(f"{label} 缺少数值")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DecompositionQueryError(f"{label} 不是有效数值") from exc
    if not result.is_finite():
        raise DecompositionQueryError(f"{label} 不是有限数值")
    return result


def calculate_symmetric_product_decomposition(
    *,
    current_metric: Decimal,
    comparison_metric: Decimal,
    current_count: Decimal,
    comparison_count: Decimal,
    overall_change: Decimal,
    count_factor_key: str = "order_count",
    count_factor_name: str = "订单数量",
    average_factor_key: str = "avg_order_amount",
    average_factor_name: str = "平均支付订单金额",
) -> tuple[list[DecompositionComponent], DecompositionReconciliation]:
    """用对称 Shapley 分解计算乘法指标的两个因素贡献。

    count 贡献 = Δcount × (avg_comparison + avg_current) / 2
    average 贡献 = Δavg × (count_comparison + count_current) / 2
    两项之和严格等于总体变化（在 Decimal 精度内）。
    """
    if comparison_count == 0 or current_count == 0:
        raise DecompositionQueryError("订单数量存在零基数，无法计算客单价拆解")
    comparison_avg = comparison_metric / comparison_count
    current_avg = current_metric / current_count
    delta_count = current_count - comparison_count
    delta_avg = current_avg - comparison_avg
    count_contribution = delta_count * (comparison_avg + current_avg) / Decimal("2")
    average_contribution = delta_avg * (comparison_count + current_count) / Decimal("2")
    tolerance = Decimal("0.01")
    summed = count_contribution + average_contribution
    reconciliation = DecompositionReconciliation(
        overall_comparison_value=comparison_metric,
        overall_current_value=current_metric,
        overall_change_amount=overall_change,
        summed_contribution_amount=summed,
        matches=abs(summed - overall_change) <= tolerance,
        tolerance=tolerance,
    )
    if not reconciliation.matches:
        raise DecompositionQueryError("指标拆解贡献与总体变化额未对账")
    components = [
        DecompositionComponent(
            factor_key=count_factor_key, factor_name=count_factor_name,
            comparison_value=comparison_count, current_value=current_count,
            change_amount=delta_count, contribution_amount=count_contribution,
            contribution_rate=None if overall_change == 0 else count_contribution / overall_change,
        ),
        DecompositionComponent(
            factor_key=average_factor_key, factor_name=average_factor_name,
            comparison_value=comparison_avg, current_value=current_avg,
            change_amount=delta_avg, contribution_amount=average_contribution,
            contribution_rate=None if overall_change == 0 else average_contribution / overall_change,
        ),
    ]
    return components, reconciliation


class MetricDecompositionTool:
    name = "metric_decomposition_query"

    @staticmethod
    def _query_text(metric_name: str, period: str, request, metric_context) -> str:
        filters = ""
        if request.filters:
            dimension_aliases = {
                item.name: (item.aliases[0] if item.aliases else item.name)
                for item in metric_context.dimensions
            }
            filters = "，筛选条件为" + "、".join(
                f"{dimension_aliases.get(item.dimension, item.dimension)}{item.operator}{item.value}"
                for item in request.filters
            )
        return (
            f"查询{metric_name}在{period}的总体值，不按维度分组{filters}；"
            "只返回一行，列名使用 value。"
        )

    @staticmethod
    async def _query_one(query_tool, metric_name, metric_key, period, role, request, metric_context, runtime):
        result = await query_tool.run(
            DataQueryRequest(
                query=MetricDecompositionTool._query_text(metric_name, period, request, metric_context),
                request_id=f"{request.request_id}:decomposition:{metric_key}:{period}",
                purpose="metric_decomposition",
                role=role,
                period=period,
                metric_key=metric_key,
                expected_columns=["value"],
                max_rows=1,
            ),
            runtime,
        )
        if not result.success:
            raise DecompositionQueryError(f"{metric_key}/{period} 查询失败：{result.error_message}")
        if not isinstance(result.rows, list) or len(result.rows) != 1:
            raise DecompositionQueryError(f"{metric_key}/{period} 未返回唯一总体值")
        return _decimal(result.rows[0].get("value"), f"{metric_key}/{period}"), result.sql or ""

    async def run(self, input_data, runtime) -> dict[str, Any]:
        formula = input_data.metric_context.decomposition
        if formula is None or formula.type != "product" or len(formula.factors) != 2:
            raise DecompositionQueryError("当前指标没有支持的乘法型双因子拆解")
        count_factor, average_factor = formula.factors
        if average_factor.source.numerator_metric_key != input_data.metric_context.metric_key:
            raise DecompositionQueryError("拆解分子未引用当前指标")
        repository = runtime.context.get("meta_mysql_repository")
        if repository is None:
            raise DecompositionQueryError("缺少 Meta Repository，无法验证依赖指标")
        count_record = await repository.get_metric_by_key(count_factor.source.metric_key)
        if count_record is None or str(getattr(count_record, "status", "")) != "published":
            raise DecompositionQueryError("数量依赖指标不存在或未发布")
        if str(getattr(count_record, "fact_table", "")) != input_data.metric_context.fact_table:
            raise DecompositionQueryError("数量依赖指标与目标指标事实表不一致")
        query_tool = DataQueryTool()
        async def query_factor(factor_key, metric_key, metric_name):
            target, target_sql = await self._query_one(
                query_tool, metric_name, metric_key, input_data.request.target_period, "目标", input_data.request, input_data.metric_context, runtime
            )
            comparison, comparison_sql = await self._query_one(
                query_tool, metric_name, metric_key, input_data.request.comparison_period, "对比", input_data.request, input_data.metric_context, runtime
            )
            return FactorPeriodValue(
                factor_key=factor_key, metric_key=metric_key, metric_name=metric_name,
                target_value=target, comparison_value=comparison,
                sql_by_period={input_data.request.target_period: target_sql, input_data.request.comparison_period: comparison_sql},
            )
        # The two factors are independent; each DataQuery invocation owns its sessions.
        count_values, metric_values = await asyncio.gather(
            query_factor(count_factor.factor_key, count_factor.source.metric_key, str(getattr(count_record, "name", count_factor.name))),
            query_factor(average_factor.source.numerator_metric_key, input_data.metric_context.metric_key, input_data.metric_context.name),
        )
        overall = input_data.inspection_outcome.overall_result
        if overall is None or overall.change_amount is None:
            raise DecompositionQueryError("缺少总体变化证据")
        components, reconciliation = calculate_symmetric_product_decomposition(
            current_metric=metric_values.target_value,
            comparison_metric=metric_values.comparison_value,
            current_count=count_values.target_value,
            comparison_count=count_values.comparison_value,
            overall_change=overall.change_amount,
            count_factor_key=count_factor.factor_key,
            count_factor_name=count_factor.name,
            average_factor_key=average_factor.factor_key,
            average_factor_name=average_factor.name,
        )
        return {
            "observations": [count_values, metric_values],
            "components": components,
            "reconciliation": reconciliation,
            "formula": formula,
        }

