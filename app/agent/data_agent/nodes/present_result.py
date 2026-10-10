"""最终输出节点：读取 State 中已处理好的结果，组装并推送 bi_report。

职责边界：
    - 不负责中文字段映射
    - 不负责数值格式化
    - 不负责图表配置本地化
    - 不负责业务洞察生成
    只负责最后的数据封装和流式输出。
"""
import re
from typing import Any, Dict, List

from langgraph.runtime import Runtime

from app.agent.data_agent.context import DataAgentContext
from app.agent.data_agent.state import DataAgentState
from app.core.logging import logger
from app.agent.data_agent.progress import emit_node_result
from app.service.result_format_service import ResultFormatService


def _safe_list(value: Any) -> list:
    """把 sql_result 统一转成 list，兼容 None、字符串等异常值。"""
    return value if isinstance(value, list) else []


def _build_source_tables(table_infos: List[Dict[str, Any]]) -> List[str]:
    """从候选表元数据中提取表名，仅作为 SQL 解析失败时的兜底。"""
    return [table.get("name") for table in table_infos if table.get("name")]


_TABLE_REF_PATTERN = re.compile(
    r"\b(?:FROM|JOIN)\s+"
    r"((?:`[^`]+`|[A-Za-z_][\w$]*)(?:\.(?:`[^`]+`|[A-Za-z_][\w$]*))?)",
    re.IGNORECASE,
)


def _extract_sql_tables(sql: str) -> List[str]:
    """从最终执行 SQL 提取实际出现在 FROM/JOIN 后的物理表名。

    table_infos 是生成 SQL 时的候选上下文，可能包含模型参考过但最终没有使用的表。
    报告展示应以最终 SQL 为准，因此这里做轻量解析；复杂 SQL 解析失败时由调用方回退
    到候选表列表，确保协议仍然兼容。
    """
    if not sql:
        return []

    tables: list[str] = []
    for match in _TABLE_REF_PATTERN.finditer(sql):
        table_name = match.group(1).replace("`", "")
        if table_name not in tables:
            tables.append(table_name)
    return tables


def _build_report_payload(
    *,
    question: str,
    formatted_data: List[Dict[str, Any]],
    sql: str,
    source_tables: List[str],
    result_analysis: Dict[str, Any],
    chart_plan: Dict[str, Any],
    chart_config: Dict[str, Any],
    insight: Dict[str, Any],
    follow_up_actions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """组装前端可消费的 bi_report 协议。"""
    return {
        "type": "bi_report",
        "question": question,
        "data": formatted_data,
        "sql": sql,
        "source_tables": source_tables,
        "row_count": len(formatted_data),
        "analysis": result_analysis,
        "chart_plan": chart_plan,
        "chart_config": chart_config,
        "insight": insight,
        "actions": follow_up_actions,
    }


async def present_result(
    state: DataAgentState,
    runtime: Runtime[DataAgentContext],
) -> Dict[str, Any]:
    writer = getattr(runtime, "stream_writer", None)

    # 并行分支汇聚时，某些 LangGraph 版本可能多次触发本节点。
    # 用 State 标记保证 bi_report 只向下游推送一次。
    if state.get("report_emitted"):
        return {}

    if writer:
        writer({"stage": "生成结构化报告"})

    query = state.get("query", "")
    sql = state.get("sql", "")
    table_infos = state.get("table_infos", [])
    sql_result = _safe_list(state.get("sql_result"))
    result_analysis = state.get("result_analysis") or {}
    chart_plan = state.get("chart_plan") or {}
    chart_config = state.get("chart_config") or {}
    insight = state.get("insight") or {}
    follow_up_actions = state.get("follow_up_actions") or []

    service = ResultFormatService()
    report_data = await service.format_report_data(
        query=query,
        table_infos=table_infos,
        sql_result=sql_result,
        chart_config=chart_config,
        provider=(runtime.context or {}).get("llm_provider", "deepseek"),
        model_tier=(runtime.context or {}).get("llm_model_tier", "deepseek-v4-flash"),
    )
    formatted_data = report_data["formatted_data"]
    localized_chart_config = report_data["chart_config"]
    source_tables = _extract_sql_tables(sql) or _build_source_tables(table_infos)

    payload = _build_report_payload(
        question=query,
        formatted_data=formatted_data,
        sql=sql,
        source_tables=source_tables,
        result_analysis=result_analysis,
        chart_plan=chart_plan,
        chart_config=localized_chart_config,
        insight=insight,
        follow_up_actions=follow_up_actions,
    )

    if writer:
        writer(payload)

    result_message = f"结构化报告生成完成：成功渲染 {len(formatted_data)} 条记录"
    logger.info("✨ [PresentResult] {}", result_message)
    emit_node_result(runtime, result_message)
    return {"report_emitted": True}
