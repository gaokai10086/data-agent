"""智能问数的只取数子图。

这个 Graph 与完整 Data Agent 共享查询前半段：语义召回、Schema 组装、SQL
生成、SQL 校验、自修正和执行；执行完成后立即结束，不生成图表、洞察或后续建议。
异动分析、定时任务等只需要原始数据结果的场景应调用这个子图。
"""

from langgraph.constants import END, START
from langgraph.graph import StateGraph

from app.agent.data_agent.context import DataAgentContext
from app.agent.data_agent.nodes.add_context import add_context
from app.agent.data_agent.nodes.column_recall import column_recall
from app.agent.data_agent.nodes.correct_sql import correct_sql
from app.agent.data_agent.nodes.example_recall import example_recall
from app.agent.data_agent.nodes.execute_sql import execute_sql
from app.agent.data_agent.nodes.extract_keywords import extract_keywords
from app.agent.data_agent.nodes.filter_table_info import filter_table_info
from app.agent.data_agent.nodes.generate_sql import generate_sql
from app.agent.data_agent.nodes.metric_recall import metric_recall
from app.agent.data_agent.nodes.merge_retrieved_info import merge_retrieved_info
from app.agent.data_agent.nodes.resolve_table_relations import resolve_table_relations
from app.agent.data_agent.nodes.translate_error import translate_error
from app.agent.data_agent.nodes.validate_sql import validate_sql
from app.agent.data_agent.nodes.value_recall import value_recall
from app.agent.data_agent.state import DataAgentState


def route_after_validation(state: DataAgentState) -> str:
    """根据 SQL 校验结果决定执行、修正还是终止。"""

    if state.get("error") is None:
        return "execute_sql"
    if state.get("retry_count", 0) >= 3:
        return "translate_error"
    return "correct_sql"


builder = StateGraph(
    state_schema=DataAgentState,
    context_schema=DataAgentContext,
)

# 查询阶段节点：与完整 Data Agent 复用同一批实现，避免维护两套 SQL 链路。
builder.add_node("extract_keywords", extract_keywords)
builder.add_node("column_recall", column_recall)
builder.add_node("value_recall", value_recall)
builder.add_node("metric_recall", metric_recall)
builder.add_node("example_recall", example_recall)
builder.add_node("merge_retrieved_info", merge_retrieved_info)
builder.add_node("resolve_table_relations", resolve_table_relations)
builder.add_node("filter_table_info", filter_table_info)
builder.add_node("add_context", add_context)
builder.add_node("generate_sql", generate_sql)
builder.add_node("validate_sql", validate_sql)
builder.add_node("correct_sql", correct_sql)
builder.add_node("translate_error", translate_error)
builder.add_node("execute_sql", execute_sql)

builder.add_edge(START, "extract_keywords")

# 关键词提取后并行召回指标、字段、枚举值和金标 SQL。
builder.add_edge("extract_keywords", "column_recall")
builder.add_edge("extract_keywords", "value_recall")
builder.add_edge("extract_keywords", "metric_recall")
builder.add_edge("extract_keywords", "example_recall")

# 汇聚召回结果，再补齐表关系和候选 Schema。
builder.add_edge("column_recall", "merge_retrieved_info")
builder.add_edge("value_recall", "merge_retrieved_info")
builder.add_edge("metric_recall", "merge_retrieved_info")
builder.add_edge("example_recall", "merge_retrieved_info")
builder.add_edge("merge_retrieved_info", "resolve_table_relations")
builder.add_edge("resolve_table_relations", "filter_table_info")
builder.add_edge("filter_table_info", "add_context")

# 生成 SQL，校验失败时进入自修正循环。
builder.add_edge("add_context", "generate_sql")
builder.add_edge("generate_sql", "validate_sql")
builder.add_conditional_edges(
    "validate_sql",
    route_after_validation,
    {
        "execute_sql": "execute_sql",
        "correct_sql": "correct_sql",
        "translate_error": "translate_error",
    },
)
builder.add_edge("correct_sql", "validate_sql")

# 只取数子图的终点是 SQL 执行；不进入图表、洞察和推荐节点。
builder.add_edge("execute_sql", END)
builder.add_edge("translate_error", END)

data_query_graph = builder.compile()

