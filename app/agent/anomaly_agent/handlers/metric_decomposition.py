"""按 Metadata 的乘法关系查询数量因素，并确定性拆解金额变化。"""
import json
from decimal import Decimal, localcontext
from hashlib import sha256
from typing import Literal

from langgraph.runtime import Runtime
from pydantic import BaseModel, Field

from app.agent.anomaly_agent.handlers.overall_comparison import _to_decimal
from app.agent.anomaly_agent.schemas import AnalysisContext, FilterCondition
from app.agent.data_agent.context import DataAgentContext
from app.agent.data_agent.query_graph import data_query_graph
from app.core.context import query_task_context
from app.repositories.mysql.dw_mysql_repository import DWMySQLRepository
from app.service.metric_decomposition_service import validate_metric_decomposition


class FactorEffect(BaseModel):
    factor_key: str
    name: str
    comparison_value: Decimal
    current_value: Decimal
    effect_amount: Decimal
    effect_rate: Decimal | None


class MetricDecompositionResult(BaseModel):
    request_id: str
    metric_key: str
    status: Literal["ready", "unavailable"]
    reason: Literal["calculated", "missing_decomposition", "missing_amount", "zero_denominator"]
    method: Literal["symmetric_two_factor"] = "symmetric_two_factor"
    target_period: str
    comparison_period: str
    scope_filters: list[FilterCondition] = Field(default_factory=list)
    comparison_amount: Decimal | None = None
    current_amount: Decimal | None = None
    change_amount: Decimal | None = None
    factors: list[FactorEffect] = Field(default_factory=list)
    sql_by_period: dict[str, str] = Field(default_factory=dict)


def _base_result(context: AnalysisContext, status: str, reason: str,
                 sql_by_period: dict[str, str] | None = None) -> MetricDecompositionResult:
    return MetricDecompositionResult(
        request_id=context.request.request_id, metric_key=context.metric.metric_key,
        status=status, reason=reason, target_period=context.request.target_period,
        comparison_period=context.request.comparison_period,
        scope_filters=context.request.filters, comparison_amount=context.comparison.comparison_value,
        current_amount=context.comparison.current_value,
        change_amount=context.comparison.change_amount, sql_by_period=sql_by_period or {},
    )


def _count(rows: object, period: str) -> Decimal:
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict) or set(rows[0]) != {"value"}:
        raise ValueError(f"{period} 订单数必须只返回一行、一列 value")
    value = _to_decimal(rows[0]["value"], f"{period}.order_count")
    if value is None or value < 0 or value != value.to_integral_value():
        raise ValueError(f"{period} 订单数必须是非负整数")
    return value


def calculate_metric_decomposition(context: AnalysisContext, current_count: Decimal,
                                   comparison_count: Decimal,
                                   sql_by_period: dict[str, str] | None = None) -> MetricDecompositionResult:
    """数量和均额的对称分摊；两因素交互项各分一半。"""
    config = context.metric.decomposition
    if config is None:
        return _base_result(context, "unavailable", "missing_decomposition", sql_by_period)
    if config.factors[1].source.numerator_metric_key != context.metric.metric_key:
        raise ValueError("拆解分子与当前指标不一致")
    now = context.comparison.current_value
    before = context.comparison.comparison_value
    delta = context.comparison.change_amount
    if now is None or before is None or delta is None or not all(x.is_finite() for x in (now, before, delta)):
        return _base_result(context, "unavailable", "missing_amount", sql_by_period)
    if abs(now - before - delta) > Decimal("0.01"):
        raise ValueError("两期金额与变化量不一致")
    for period, count in ((context.request.target_period, current_count),
                          (context.request.comparison_period, comparison_count)):
        if not isinstance(count, Decimal) or not count.is_finite() or count < 0 or count != count.to_integral_value():
            raise ValueError(f"{period} 订单数必须是非负整数")
    if not current_count or not comparison_count:
        return _base_result(context, "unavailable", "zero_denominator", sql_by_period)

    with localcontext() as arithmetic:
        arithmetic.prec = 50
        avg_now, avg_before = now / current_count, before / comparison_count
        count_effect = (current_count - comparison_count) * (avg_now + avg_before) / 2
        average_effect = (avg_now - avg_before) * (current_count + comparison_count) / 2
        if abs(count_effect + average_effect - delta) > Decimal("0.01"):
            raise ValueError("拆解贡献与总体变化量无法对账")
        count_factor, average_factor = config.factors
        factors = [
            FactorEffect(factor_key=count_factor.factor_key, name=count_factor.name,
                         comparison_value=comparison_count, current_value=current_count,
                         effect_amount=count_effect, effect_rate=count_effect / delta if delta else None),
            FactorEffect(factor_key=average_factor.factor_key, name=average_factor.name,
                         comparison_value=avg_before, current_value=avg_now,
                         effect_amount=average_effect, effect_rate=average_effect / delta if delta else None),
        ]
    return _base_result(context, "ready", "calculated", sql_by_period).model_copy(update={"factors": factors})


async def execute_metric_decomposition(context: AnalysisContext,
                                       runtime: Runtime[DataAgentContext]) -> MetricDecompositionResult:
    """仅查询两期已发布订单数；金额直接使用传入的总体或下钻范围结果。"""
    config = context.metric.decomposition
    if config is None:
        return _base_result(context, "unavailable", "missing_decomposition")
    if context.comparison.current_value is None or context.comparison.comparison_value is None:
        return _base_result(context, "unavailable", "missing_amount")
    repository = runtime.context.get("meta_mysql_repository")
    factory = runtime.context.get("dw_session_factory")
    if repository is None or factory is None:
        raise RuntimeError("指标拆解缺少 Metadata 仓储或 dw_session_factory")
    await validate_metric_decomposition(context.metric, config, repository)
    count_metric = await repository.get_metric_by_key(config.factors[0].source.metric_key)
    scope = context.request.model_dump(mode="json")
    scope_id = sha256(json.dumps(scope["filters"], sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
    sqls: dict[str, str] = {}
    counts: list[Decimal] = []
    for period, role in ((context.request.target_period, "目标"),
                         (context.request.comparison_period, "对比")):
        query = (
            f"查询已发布指标 {count_metric.name}（metric_key={count_metric.metric_key}）在 {period} 的总体值。"
            f"指标定义：{json.dumps({'fact_table': count_metric.fact_table, 'measure_column': count_metric.measure_column, 'aggregation': count_metric.aggregation, 'time_column': count_metric.time_column, 'filters': count_metric.filters}, ensure_ascii=False)}。"
            f"继承目标金额指标的同一分析范围，原始请求：{json.dumps(scope, ensure_ascii=False)}。"
            "严格保留全部用户筛选、指标默认筛选及本月时间边界；不按维度分组。"
            "只返回一行、一列，列名为 value。"
        )
        with query_task_context(request_id=context.request.request_id,
                                role=f"指标拆解:{scope_id}:{role}", period=period,
                                metric_key=count_metric.metric_key):
            async with factory() as session:
                query_context = dict(runtime.context)
                query_context["dw_mysql_repository"] = DWMySQLRepository(session)
                result = {}
                async for mode, chunk in data_query_graph.astream(
                    input={"query": query}, context=query_context, stream_mode=["custom", "values"],
                ):
                    if mode == "values":
                        result = chunk
                    elif isinstance(chunk, dict):
                        runtime.stream_writer({**chunk, "period": period,
                                               "metric_key": count_metric.metric_key,
                                               "scope_filters": scope["filters"], "scope_id": scope_id})
                if result.get("error_response") or result.get("error"):
                    raise RuntimeError(f"{period} 订单数取数失败，请检查查询日志")
                if not result.get("sql"):
                    raise ValueError(f"{period} 订单数取数缺少 SQL 证据")
                counts.append(_count(result.get("sql_result"), period))
                sqls[period] = result["sql"]
    return calculate_metric_decomposition(context, counts[0], counts[1], sqls)
