"""通过智能问数 Data Agent 查询两个比较周期的总体指标值。

异动 Agent 只负责把分析意图整理成数据查询问题；指标召回、Schema 召回、SQL
生成、SQL 校验和数据库执行全部复用 Data Agent 的既有链路。
"""

from __future__ import annotations

from typing import Any

from langgraph.runtime import Runtime

from app.agent.anomaly_agent.schemas import AnalysisRequest, MetricContext, OverallQueryResult
from app.agent.anomaly_agent.state import AnomalyState
from app.agent.data_agent.context import DataAgentContext
from app.agent.data_agent.query_graph import data_query_graph
from app.agent.data_agent.state import DataAgentState


def build_overall_query(request: AnalysisRequest, metric_context: MetricContext) -> str:
    """把结构化异动请求转换成 Data Agent 能理解的自然语言查询。

    这里不拼接 SQL。Data Agent 会根据共享 Metadata 自己完成指标召回、字段关联、
    SQL 生成、校验和执行，因此更换指标或比较周期时不需要修改本节点的 SQL 逻辑。
    """

    filter_text = ""
    if request.filters:
        filter_text = "，筛选条件为" + "、".join(
            f"{item.dimension}{item.operator}{item.value}" for item in request.filters
        )

    return (
        f"查询{metric_context.name}在{request.target_period}和"
        f"{request.comparison_period}的总体值，不按维度分组"
        f"{filter_text}；结果按目标周期和对比周期分别返回，"
        "列名使用 current_value 和 comparison_value。"
    )


def _extract_overall_values(rows: Any) -> tuple[Any, Any]:
    """从 Data Agent 的 SQL 结果中读取目标周期和对比周期的值。"""

    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError(f"总体查询应返回一行结构化结果，实际结果为：{rows!r}")

    row = rows[0]
    if "current_value" not in row or "comparison_value" not in row:
        raise ValueError(
            "Data Agent 总体查询结果缺少 current_value 或 comparison_value，"
            f"实际字段为：{list(row)}"
        )
    return row["current_value"], row["comparison_value"]


async def query_overall(
    state: AnomalyState,
    runtime: Runtime[DataAgentContext],
) -> dict[str, object]:
    """调用 Data Agent 查询目标周期和对比周期的总体指标值。"""

    request: AnalysisRequest | None = state.get("analysis_request")
    metric_context: MetricContext | None = state.get("metric_context")
    if request is None or metric_context is None:
        raise ValueError("总体查询前必须先完成请求解析和指标上下文加载")

    # 复用智能问数完整链路，而不是在异动 Agent 内部重新实现 SQL 查询。
    query = build_overall_query(request, metric_context)
    result: dict[str, Any] = {}
    async for mode, chunk in data_query_graph.astream(
        input=DataAgentState(query=query),
        context=runtime.context,
        stream_mode=["custom", "values"],
    ):
        if mode == "custom":
            # 将 Data Agent 的进度和结果事件继续交给上层调用方。
            runtime.stream_writer(chunk)
        elif mode == "values":
            result = chunk

    current_value, comparison_value = _extract_overall_values(result.get("sql_result"))
    return {
        "overall_result": OverallQueryResult(
            request_id="overall_metric",
            metric_key=metric_context.metric_key,
            target_period=request.target_period,
            comparison_period=request.comparison_period,
            current_value=current_value,
            comparison_value=comparison_value,
        )
    }
