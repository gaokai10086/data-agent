"""BusinessDiagnosisAgent 的受控验证工具。

工具只接受上一轮真实 AttributionPath 提供的父级成员，查询仍通过现有
DataQueryTool，金额计算复用 MetricDecompositionAgent 的确定性实现。
"""

from __future__ import annotations

import asyncio
import re
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Any

from app.agent.anomaly_agent.MetricDecomposition_Agent.tools import calculate_symmetric_product_decomposition
from app.agent.anomaly_agent.common.data_query import DataQueryRequest, DataQueryTool


class ScopedMetricDecompositionError(RuntimeError):
    def __init__(self, message: str, query_count: int = 0):
        super().__init__(message)
        self.query_count = query_count


def _decimal(value: Any, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ScopedMetricDecompositionError(f"{label} 不是有效数值") from exc
    if not result.is_finite():
        raise ScopedMetricDecompositionError(f"{label} 不是有限数值")
    return result


class ScopedMetricDecompositionTool:
    name = "scoped_metric_decomposition"

    @staticmethod
    def _period_bounds(period: str) -> tuple[str, str]:
        match = re.fullmatch(r"(\d{4})-(\d{2})", period)
        if not match:
            raise ScopedMetricDecompositionError(f"无效周期：{period}")
        year, month = int(match.group(1)), int(match.group(2))
        end = f"{year + 1:04d}-01-01" if month == 12 else f"{year:04d}-{month + 1:02d}-01"
        return f"{year:04d}-{month:02d}-01", end

    @staticmethod
    def _scope_text(scope: list[dict[str, Any]]) -> str:
        return "、".join(
            f"{item['dimension_column']}={'NULL' if item.get('member_value') is None else item['member_value']}"
            for item in scope
        )

    @classmethod
    def _query_text(cls, metric_name: str, period: str, request, metric_context, scope: list[dict[str, Any]]) -> str:
        fixed_parts = []
        for item in request.filters:
            matched = next(
                (dimension for dimension in metric_context.dimensions
                 if dimension.name == item.dimension or item.dimension in dimension.aliases or dimension.column == item.dimension),
                None,
            )
            # 使用 Meta 已确认的业务名称/别名表达固定筛选；由现有 Grounding
            # 将其绑定到 matched.column，避免把物理列名当成用户字段再次解析。
            dimension_name = (matched.aliases[0] if matched and matched.aliases else matched.name) if matched else item.dimension
            fixed_parts.append(f"{dimension_name}{item.operator}{item.value}")
        fixed = "、".join(fixed_parts)
        filters = "；固定筛选：" + fixed if fixed else ""
        parent = f"；已验证父级范围：{cls._scope_text(scope)}" if scope else ""
        return f"查询{metric_name}在{period}的总体值，不按维度分组{filters}{parent}；只返回一行，列名使用 value。"

    @classmethod
    def _validate_sql(cls, sql: str | None, period: str, request, scope: list[dict[str, Any]]) -> None:
        if not sql:
            raise ScopedMetricDecompositionError(f"{period} 未返回 SQL")
        normalized = sql.casefold()
        start, end = cls._period_bounds(period)
        if start not in sql or end not in sql:
            raise ScopedMetricDecompositionError(f"{period} SQL 时间范围不符合请求")
        for item in request.filters:
            if str(item.value) not in sql:
                raise ScopedMetricDecompositionError(f"SQL 未保留固定筛选：{item.value}")
        for item in scope:
            value = item.get("member_value")
            dimension_column = str(item.get("dimension_column") or "").split(".")[-1].casefold()
            if dimension_column and dimension_column not in normalized:
                raise ScopedMetricDecompositionError(f"SQL 未保留父级维度字段：{item.get('dimension_column')}")
            if value is not None and str(value) not in sql:
                raise ScopedMetricDecompositionError(f"SQL 未保留已验证父级成员：{value}")
            if value is None and "is null" not in normalized:
                raise ScopedMetricDecompositionError("SQL 未正确保留 NULL 父级成员范围")
        if "order_status" not in normalized:
            raise ScopedMetricDecompositionError("SQL 未保留已发布订单状态过滤")

    @staticmethod
    def _candidate_scope(input_data, args: dict[str, Any]):
        dimension = args.get("dimension_column")
        member = args.get("member_value")
        evidence_id = args.get("parent_evidence_id")
        if not dimension or not evidence_id:
            raise ScopedMetricDecompositionError("验证任务必须指定父级证据和维度")
        if dimension not in {item.column for item in input_data.metric_context.dimensions}:
            raise ScopedMetricDecompositionError("父级维度不在当前 Meta 允许范围内")
        attribution = input_data.attribution_result
        if attribution is None:
            raise ScopedMetricDecompositionError("缺少真实维度归因证据")
        from app.agent.anomaly_agent.BusinessDiagnosis_Agent.schemas import evidence_id as make_evidence_id
        for path in attribution.paths:
            path_evidence_id = make_evidence_id("dimension_attribution", path.model_dump(mode="json"))
            if path_evidence_id != evidence_id or path.dimension_column != dimension:
                continue
            if not any(item.group_value == member for item in path.attributions):
                raise ScopedMetricDecompositionError("父级成员不在真实 AttributionPath 中")
            selected = next(item for item in path.attributions if item.group_value == member)
            return (
                [scope.model_dump(mode="json") for scope in path.parent_scope] + [{"dimension_column": dimension, "member_value": member}],
                selected,
            )
        raise ScopedMetricDecompositionError("父级证据 ID 不属于当前真实 AttributionPath")

    async def run(self, input_data, runtime, *, tool_arguments: dict[str, Any], query_budget_remaining: int) -> dict[str, Any]:
        if query_budget_remaining < 4:
            raise ScopedMetricDecompositionError("查询预算不足，范围拆解需要实际执行 4 条 SQL")
        formula = input_data.metric_context.decomposition
        if formula is None or formula.type != "product" or len(formula.factors) != 2:
            raise ScopedMetricDecompositionError("当前指标没有支持的乘法型双因子拆解")
        count_factor, average_factor = formula.factors
        if average_factor.source.numerator_metric_key != input_data.metric_context.metric_key:
            raise ScopedMetricDecompositionError("拆解分子未引用当前指标")
        scope, parent_member = self._candidate_scope(input_data, tool_arguments)
        request_data = input_data.analysis_request
        repository = (runtime.context or {}).get("meta_mysql_repository")
        if repository is None:
            raise ScopedMetricDecompositionError("缺少 Meta Repository，无法验证依赖指标")
        count_record = await repository.get_metric_by_key(count_factor.source.metric_key)
        if count_record is None or str(getattr(count_record, "status", "")) != "published":
            raise ScopedMetricDecompositionError("数量依赖指标不存在或未发布")
        if str(getattr(count_record, "fact_table", "")) != input_data.metric_context.fact_table:
            raise ScopedMetricDecompositionError("数量依赖指标与目标指标事实表不一致")
        query_tool = DataQueryTool()
        query_runtime = SimpleNamespace(context=runtime.context, stream_writer=runtime.stream_writer)
        query_count = 0
        values: dict[str, dict[str, Any]] = {}

        async def query(metric_key: str, metric_name: str, period: str, role: str) -> None:
            nonlocal query_count
            result = await query_tool.run(DataQueryRequest(
                query=self._query_text(metric_name, period, request_data, input_data.metric_context, scope),
                request_id=f"{request_data.request_id}:scoped:{metric_key}:{period}:{tool_arguments.get('member_value')}",
                purpose=self.name, role=role, period=period, metric_key=metric_key,
                expected_columns=["value"], max_rows=1,
            ), query_runtime)
            query_count += 1
            if not result.success or not isinstance(result.rows, list) or len(result.rows) != 1:
                raise ScopedMetricDecompositionError(f"{metric_key}/{period} 查询失败：{result.error_message}", query_count)
            self._validate_sql(result.sql, period, request_data, scope)
            values.setdefault(metric_key, {})[period] = {"value": _decimal(result.rows[0].get("value"), f"{metric_key}/{period}"), "sql": result.sql or ""}

        query_coroutines = (
            query(count_factor.source.metric_key, count_factor.name, request_data.target_period, "目标"),
            query(count_factor.source.metric_key, count_factor.name, request_data.comparison_period, "对比"),
            query(input_data.metric_context.metric_key, input_data.metric_context.name, request_data.target_period, "目标"),
            query(input_data.metric_context.metric_key, input_data.metric_context.name, request_data.comparison_period, "对比"),
        )
        query_tasks = [asyncio.create_task(coroutine) for coroutine in query_coroutines]
        try:
            await asyncio.gather(*query_tasks)
        except BaseException:
            for task in query_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*query_tasks, return_exceptions=True)
            raise
        try:
            current_metric = values[input_data.metric_context.metric_key][request_data.target_period]["value"]
            comparison_metric = values[input_data.metric_context.metric_key][request_data.comparison_period]["value"]
            current_count = values[count_factor.source.metric_key][request_data.target_period]["value"]
            comparison_count = values[count_factor.source.metric_key][request_data.comparison_period]["value"]
            if abs(current_metric - parent_member.current_value) > Decimal("0.01") or abs(comparison_metric - parent_member.comparison_value) > Decimal("0.01"):
                raise ScopedMetricDecompositionError("范围销售额与 AttributionPath 成员证据不一致", query_count)
            components, reconciliation = calculate_symmetric_product_decomposition(
                current_metric=current_metric, comparison_metric=comparison_metric,
                current_count=current_count, comparison_count=comparison_count,
                overall_change=current_metric - comparison_metric,
                count_factor_key=count_factor.factor_key, count_factor_name=count_factor.name,
                average_factor_key=average_factor.factor_key, average_factor_name=average_factor.name,
            )
            return {"scope": scope, "member_value": tool_arguments.get("member_value"), "dimension_column": tool_arguments.get("dimension_column"), "query_count": query_count, "components": components, "reconciliation": reconciliation, "observations": values}
        except ScopedMetricDecompositionError as exc:
            exc.query_count = max(exc.query_count, query_count)
            raise
        except Exception as exc:
            raise ScopedMetricDecompositionError(str(exc), query_count) from exc


DIAGNOSIS_TOOL_REGISTRY = {ScopedMetricDecompositionTool.name: ScopedMetricDecompositionTool()}
