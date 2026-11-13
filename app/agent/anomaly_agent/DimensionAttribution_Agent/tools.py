"""DimensionAttributionAgent 的真实工具；不接入 RootCause ActionRegistry。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any
from types import SimpleNamespace

from app.agent.anomaly_agent.DimensionAttribution_Agent.schemas import (
    DimensionScope,
    DimensionMemberAttribution,
    ReconciliationResult,
)
from app.core.sql_budget import sql_attempt_budget
from app.agent.anomaly_agent.common.data_query import DataQueryRequest, DataQueryTool


class AttributionQueryError(RuntimeError):
    def __init__(self, message: str, actual_sql_count: int):
        super().__init__(message)
        self.actual_sql_count = actual_sql_count


class DimensionPeriodComparisonTool:
    name = "dimension_period_comparison"

    @staticmethod
    def _decimal(value: Any) -> Decimal:
        try:
            result = Decimal(str(value or 0))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError(f"分组值不是有效数值：{value!r}") from exc
        if not result.is_finite():
            raise ValueError(f"分组值不是有限数值：{value!r}")
        return result

    @staticmethod
    def _filters_text(request) -> str:
        if not request.filters:
            return ""
        return "，固定筛选条件为" + "、".join(
            f"{item.dimension}{item.operator}{item.value}" for item in request.filters
        )

    def _query_text(self, request, metric, dimension, period, role, parent_scope) -> str:
        parent_text = ""
        if parent_scope:
            parent_text = "，并且必须保留已验证父级范围：" + "、".join(
                f"{item.dimension_column} {'IS NULL' if item.member_value is None else '=' + item.member_value}"
                for item in parent_scope
            )
        return (
            f"查询{metric.name}在{period}的总体值，按{dimension.name}分组，"
            f"分组字段为{dimension.column}{self._filters_text(request)}{parent_text}；"
            f"这是{role}周期查询。每个分组一行，必须返回两列："
            "dimension_value 和 value；空分组保留为 NULL。"
        )

    @staticmethod
    def _period_bounds(period: str) -> tuple[str, str]:
        match = re.fullmatch(r"(\d{4})-(\d{2})", period)
        if not match:
            raise ValueError(f"无效周期：{period}")
        year, month = int(match.group(1)), int(match.group(2))
        if month == 12:
            end = f"{year + 1:04d}-01-01"
        else:
            end = f"{year:04d}-{month + 1:02d}-01"
        return f"{year:04d}-{month:02d}-01", end

    @classmethod
    def _validate_sql_contract(cls, sql: str | None, request, metric, dimension, period, parent_scope) -> None:
        """校验 LLM SQL 是否仍遵守 Meta 指标、周期和范围契约。"""
        if not sql:
            raise ValueError(f"周期 {period} 未返回 SQL")
        # A row cap can silently drop zero-valued dimension members while sums
        # still reconcile. Attribution must inspect the complete grouping.
        sql_without_comments = re.sub(r"/\*.*?\*/|--[^\n]*", " ", sql, flags=re.DOTALL)
        if re.search(r"\bLIMIT\b", sql_without_comments, flags=re.IGNORECASE):
            raise ValueError(f"周期 {period} SQL 包含 LIMIT，分组可能不完整")
        normalized = sql.casefold()
        start, end = cls._period_bounds(period)
        required_tokens = (metric.fact_table, metric.measure_column, dimension.column.split(".")[-1], "group by")
        if any(token.casefold() not in normalized for token in required_tokens):
            raise ValueError(f"周期 {period} SQL 未完整保留指标或分组口径")
        if "sum(" not in normalized:
            raise ValueError(f"周期 {period} SQL 未使用指标聚合表达式")
        if start not in sql or end not in sql:
            raise ValueError(f"周期 {period} SQL 时间范围不符合请求")
        for item in request.filters:
            if item.value and str(item.value) not in sql:
                raise ValueError(f"周期 {period} SQL 未保留固定筛选值：{item.value}")
        for item in parent_scope:
            if item.member_value is not None and item.member_value not in sql:
                raise ValueError(f"周期 {period} SQL 未保留父级成员：{item.member_value}")

    @staticmethod
    def _normalize_rows(rows: list[dict[str, Any]], period: str) -> dict[str | None, Decimal]:
        values: dict[str | None, Decimal] = {}
        for row in rows:
            if "value" not in row:
                raise ValueError(f"周期 {period} 分组结果缺少 value 字段")
            group_key = row.get("dimension_value")
            if group_key is None:
                non_value = [key for key in row if key != "value"]
                if len(non_value) == 1:
                    group_key = row[non_value[0]]
            group = None if group_key is None else str(group_key)
            values[group] = values.get(group, Decimal("0")) + DimensionPeriodComparisonTool._decimal(row["value"])
        return values

    @staticmethod
    def compute_attributions(current, comparison, overall):
        """纯确定性计算与对账逻辑，供工具和单元测试复用。"""
        if overall is None or overall.current_value is None or overall.comparison_value is None or overall.change_amount is None:
            raise ValueError("缺少总体变化证据，不能执行维度对账")
        tolerance = Decimal("0.01")
        members: list[DimensionMemberAttribution] = []
        for group in sorted(set(current) | set(comparison), key=lambda item: "" if item is None else item):
            comparison_value = comparison.get(group, Decimal("0"))
            current_value = current.get(group, Decimal("0"))
            change_amount = current_value - comparison_value
            change_rate = None if comparison_value == 0 else change_amount / comparison_value
            contribution_rate = None if overall.change_amount == 0 else change_amount / overall.change_amount
            members.append(DimensionMemberAttribution(
                group_value=group, comparison_value=comparison_value,
                current_value=current_value, change_amount=change_amount,
                change_rate=change_rate, net_contribution_rate=contribution_rate,
            ))
        comparison_sum = sum((item.comparison_value for item in members), Decimal("0"))
        current_sum = sum((item.current_value for item in members), Decimal("0"))
        summed_change = sum((item.change_amount for item in members), Decimal("0"))
        reconciliation = ReconciliationResult(
            comparison_sum=comparison_sum, current_sum=current_sum,
            summed_change_amount=summed_change,
            overall_comparison_value=overall.comparison_value,
            overall_current_value=overall.current_value,
            overall_change_amount=overall.change_amount,
            comparison_matches=abs(comparison_sum - overall.comparison_value) <= tolerance,
            current_matches=abs(current_sum - overall.current_value) <= tolerance,
            change_matches=abs(summed_change - overall.change_amount) <= tolerance,
            tolerance=tolerance,
        )
        if not (reconciliation.comparison_matches and reconciliation.current_matches and reconciliation.change_matches):
            raise ValueError("维度分组结果与总体变化未对账")
        return members, reconciliation

    async def run(
        self,
        input_data,
        runtime,
        *,
        dimension_column: str | None = None,
        parent_scope: list[DimensionScope] | None = None,
        task_id: str | None = None,
        depth: int = 0,
        overall=None,
        query_budget_remaining: int | None = None,
    ) -> dict[str, Any]:
        request = input_data.request
        metric = input_data.metric_context
        dimension = self.resolve_dimension(input_data, dimension_column=dimension_column)
        parent_scope = list(parent_scope or [])
        allowed_columns = {item.column for item in metric.dimensions}
        if any(item.dimension_column not in allowed_columns for item in parent_scope):
            raise ValueError("父级范围包含不在 MetricContext 中的维度")
        query_tool = DataQueryTool()
        def track_event(event):
            runtime.stream_writer(event)

        query_runtime = SimpleNamespace(context=runtime.context, stream_writer=track_event)

        async def one(period: str, role: str):
            return await query_tool.run(
                DataQueryRequest(
                    query=self._query_text(request, metric, dimension, period, role, parent_scope),
                    request_id=f"{request.request_id}:{task_id or 'pending'}:{period}",
                    purpose=self.name,
                    role=role,
                    period=period,
                    metric_key=metric.metric_key,
                    expected_columns=["dimension_value", "value"],
                    max_rows=1000,
                ),
                query_runtime,
            )

        try:
            with sql_attempt_budget(query_budget_remaining if query_budget_remaining is not None else 1_000_000) as budget:
                current_result, comparison_result = await asyncio.gather(
                    one(request.target_period, "目标"),
                    one(request.comparison_period, "对比"),
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise AttributionQueryError(str(exc), budget.count) from exc
        sql_count = budget.count
        if not current_result.success:
            raise AttributionQueryError(f"目标周期分组查询失败：{current_result.error_message}", sql_count)
        if not comparison_result.success:
            raise AttributionQueryError(f"对比周期分组查询失败：{comparison_result.error_message}", sql_count)

        try:
            self._validate_sql_contract(
                current_result.sql, request, metric, dimension, request.target_period, parent_scope
            )
            self._validate_sql_contract(
                comparison_result.sql, request, metric, dimension, request.comparison_period, parent_scope
            )

            current = self._normalize_rows(current_result.rows, request.target_period)
            comparison = self._normalize_rows(comparison_result.rows, request.comparison_period)
            overall = overall or input_data.inspection_outcome.overall_result
            members, reconciliation = self.compute_attributions(current, comparison, overall)
            non_null_members = {
                item.group_value for item in members if item.group_value is not None
            }
            informative = not (
                len(non_null_members) == 1
                and reconciliation.comparison_matches
                and reconciliation.current_matches
            )
        except Exception as exc:
            raise AttributionQueryError(str(exc), sql_count) from exc

        return {
            "dimension": dimension,
            "task_id": task_id,
            "depth": depth,
            "parent_scope": parent_scope,
            "members": members,
            "reconciliation": reconciliation,
            "sql_by_period": {
                request.target_period: current_result.sql or "",
                request.comparison_period: comparison_result.sql or "",
            },
            "query_count": sql_count,
            "informative": informative,
            "null_member_present": None in current or None in comparison,
        }

    @staticmethod
    def find_dimension_matches(input_data):
        dimensions = input_data.metric_context.dimensions
        matches = []
        for dimension in dimensions:
            expressions = {dimension.name, *dimension.aliases, dimension.column}
            if any(expression and expression in input_data.raw_question for expression in expressions):
                matches.append(dimension)
        return matches

    @classmethod
    def resolve_dimension(cls, input_data, *, dimension_column: str | None = None):
        dimensions = input_data.metric_context.dimensions
        if dimension_column:
            matches = [item for item in dimensions if item.column == dimension_column]
            if len(matches) != 1:
                raise ValueError("选择的维度不在当前指标 Meta 允许范围内")
            return matches[0]
        matches = cls.find_dimension_matches(input_data)
        if len(matches) != 1:
            raise ValueError("无法从 Meta 唯一确定用户指定的分析维度")
        return matches[0]

    @staticmethod
    def build_task_id(input_data, dimension_column: str, parent_scope, depth: int) -> str:
        payload = {
            "metric_key": input_data.metric_context.metric_key,
            "target_period": input_data.request.target_period,
            "comparison_period": input_data.request.comparison_period,
            "filters": [item.model_dump(mode="json") for item in input_data.request.filters],
            "dimension_column": dimension_column,
            "parent_scope": [item.model_dump(mode="json") for item in parent_scope],
            "depth": depth,
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:24]


ATTRIBUTION_TOOL_REGISTRY = {
    DimensionPeriodComparisonTool.name: DimensionPeriodComparisonTool(),
}
