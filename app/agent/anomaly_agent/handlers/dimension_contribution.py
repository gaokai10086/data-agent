"""维度工具：复用只取数子图，顺序查询两期，确定性计算并对账。"""
import json
from hashlib import sha256
from decimal import Decimal

from langgraph.runtime import Runtime

from app.agent.anomaly_agent.handlers.overall_comparison import _to_decimal
from app.agent.anomaly_agent.schemas import AnalysisContext, GroupContribution
from app.agent.data_agent.query_graph import data_query_graph
from app.agent.data_agent.context import DataAgentContext
from app.core.context import query_task_context
from app.repositories.mysql.dw_mysql_repository import DWMySQLRepository


MAX_GROUPS = 100
RECONCILIATION_TOLERANCE = Decimal("0.01")


def parse_groups(rows: object) -> dict[str | None, Decimal]:
    if not isinstance(rows, list) or not rows or len(rows) > MAX_GROUPS:
        raise ValueError("分组结果为空或超过 100 组，无法确认完整性")
    groups = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"group_value", "value"}:
            raise ValueError("分组查询必须只返回 group_value、value 两列")
        key = row["group_value"]
        if key is not None and not isinstance(key, str):
            raise ValueError("第一版只支持文本或 NULL 维度分组")
        if key in groups:
            raise ValueError("分组键重复，拒绝覆盖数据")
        value = _to_decimal(row["value"], "group.value")
        if value is None:
            raise ValueError("分组指标缺失，不能作为零处理")
        groups[key] = value
    return groups


def calculate_contributions(context: AnalysisContext, current_rows: object,
                            comparison_rows: object) -> list[GroupContribution]:
    # 新增或消失分组下钻后，一期可能完全无记录；只有父分组基准为零才允许空结果。
    current = {} if current_rows == [] and context.comparison.current_value == 0 else parse_groups(current_rows)
    comparison = ({} if comparison_rows == [] and context.comparison.comparison_value == 0
                  else parse_groups(comparison_rows))
    for groups, total in ((current, context.comparison.current_value),
                          (comparison, context.comparison.comparison_value)):
        if total is None or not total.is_finite() or abs(sum(groups.values(), Decimal(0)) - total) > RECONCILIATION_TOLERANCE:
            raise ValueError("分组汇总与当前范围基准（总体值或父分组值）不一致：可能存在截断、Join 重复或口径偏差")
    net = context.comparison.change_amount
    if net is None or not net.is_finite():
        raise ValueError("总体变化量不可用")
    grouped_delta = sum(current.values(), Decimal(0)) - sum(comparison.values(), Decimal(0))
    if abs(grouped_delta - net) > RECONCILIATION_TOLERANCE:
        raise ValueError("分组变化量与总体变化量不一致")
    results = []
    # 无记录的一期以零补齐；查询返回的 NULL 数值则已在 parse_groups 拒绝。
    for key in dict.fromkeys([*current, *comparison]):
        now, before = current.get(key, Decimal(0)), comparison.get(key, Decimal(0))
        delta = now - before
        results.append(GroupContribution(
            group_value=key, current_value=now, comparison_value=before,
            change_amount=delta, contribution_rate=delta / net if net else None,
        ))
    return sorted(results, key=lambda item: abs(item.change_amount), reverse=True)


async def query_dimension(context: AnalysisContext, column: str,
                          runtime: Runtime[DataAgentContext]) -> tuple[list[GroupContribution], dict[str, str]]:
    """按顺序调用子图，避免并发复用 Context 内的 Meta AsyncSession。"""
    factory = runtime.context.get("dw_session_factory")
    if factory is None:
        raise RuntimeError("维度分析缺少 dw_session_factory")
    results, sqls = [], {}
    scope = context.request.model_dump(mode="json")
    # 同一维度可能在多家门店分别执行，任务标识必须区分查询范围。
    scope_id = sha256(json.dumps(scope["filters"], sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
    filter_text = "、".join(
        f"{item.dimension}{item.operator}{item.value}" for item in context.request.filters
    ) or "无额外用户筛选"
    for period, role in ((context.request.target_period, "目标"),
                         (context.request.comparison_period, "对比")):
        query = (
            f"查询{context.metric.name}在{period}按字段 {column} 分组的指标值。"
            f"筛选条件为{filter_text}；"
            f"原始请求：{json.dumps(scope, ensure_ascii=False)}。"
            f"已发布指标口径：{context.metric.model_dump_json()}。"
            "仅查询本次指定月份，严格保留原请求全部筛选和指标默认筛选。"
            "按完整维度值分组，不做 TopN，不丢弃 NULL 维度；"
            f"最多返回 {MAX_GROUPS + 1} 组以检测超限；"
            "只返回两列：维度原值 AS group_value，聚合值 AS value。"
        )
        with query_task_context(request_id=context.request.request_id,
                                role=f"维度:{column}:{scope_id}:{role}", period=period,
                                metric_key=context.metric.metric_key):
            async with factory() as session:
                query_context = dict(runtime.context)
                query_context["dw_mysql_repository"] = DWMySQLRepository(session)
                result = {}
                async for mode, chunk in data_query_graph.astream(
                    input={"query": query}, context=query_context,
                    stream_mode=["custom", "values"],
                ):
                    if mode == "values":
                        result = chunk
                    elif isinstance(chunk, dict):
                        runtime.stream_writer({**chunk, "dimension_column": column, "period": period,
                                               "scope_filters": scope["filters"], "scope_id": scope_id})
                if result.get("error_response") or result.get("error"):
                    raise RuntimeError("维度取数失败，请检查查询日志")
                if not result.get("sql"):
                    raise ValueError("维度取数缺少 SQL 证据")
                results.append(result.get("sql_result"))
                sqls[period] = result["sql"]
    return calculate_contributions(context, *results), sqls
