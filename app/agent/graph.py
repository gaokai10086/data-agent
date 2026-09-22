"""
================================================================================
模块名称：智能问数状态图总指挥 (Agent Workflow Orchestrator)
模块路径：app/agent/graph.py
核心职责：
    1. 前置意图分流（闲聊直答 vs 问数链路）
    2. 四路异步并行召回与特征汇聚屏障（Fan-out / Fan-in：列名、枚举、指标、金标SQL 四路并发）
    3. 生成-质检-纠错自反思闭环（Generate-Validate-Correct Loop）
    4. 物理执行取数与独立展示层解耦（Execute -> Present -> END）
    5. 预留二期图表生成与商业洞察并行插槽
================================================================================
"""
import asyncio
from langgraph.constants import START, END
from langgraph.graph import StateGraph

# 上下文与状态
from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState

# 节点引入
from app.agent.nodes.intent_detection import intent_detection
from app.agent.nodes.direct_answer import direct_answer
from app.agent.nodes.extract_keywords import extract_keywords
from app.agent.nodes.column_recall import column_recall
from app.agent.nodes.value_recall import value_recall
from app.agent.nodes.metric_recall import metric_recall
from app.agent.nodes.example_recall import example_recall  # 🌟 引入金标 SQL 并发召回节点
from app.agent.nodes.merge_retrieved_info import merge_retrieved_info
from app.agent.nodes.resolve_table_relations import resolve_table_relations
from app.agent.nodes.filter_table_info import filter_table_info
from app.agent.nodes.add_context import add_context
from app.agent.nodes.generate_sql import generate_sql
from app.agent.nodes.validate_sql import validate_sql
from app.agent.nodes.correct_sql import correct_sql
from app.agent.nodes.execute_sql import execute_sql
from app.agent.nodes.present_result import present_result  # 独立展示节点
from app.agent.nodes.translate_error import translate_error

# 客户端与仓储引入
from app.clients.qdrant_client import qdrant_client_manager
from app.clients.es_client import es_client_manager
from app.clients.mysql_client import meta_client_manager, dw_client_manager
from app.clients.embedding_client import embedding_client_manager
from app.core.context import request_id_ctx_var
from app.repositories.es.value_es_repository import ValueESRepository
from app.repositories.mysql.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository
from app.repositories.qdrant.column_repository_qdrant import ColumnQdrantRepository
from app.repositories.qdrant.metric_repository_qdrant import MetricQdrantRepository
from app.repositories.qdrant.sql_example_repository import SQLExampleQdrantRepository


# ==================== 条件路由决策函数 ====================

def route_intent(state: DataAgentState) -> str:
    """意图识别后置路由：决定走直答还是问数链路"""
    if state.get("intent") == "direct_answer":
        return "direct_answer"
    return "extract_keywords"


def route_after_validation(state: DataAgentState) -> str:
    """SQL 质检后置路由：决定取数、纠错重试还是熔断翻译"""
    if state.get("error") is None:
        return "execute_sql"
    elif state.get("retry_count", 0) >= 3:
        return "translate_error"
    else:
        return "correct_sql"


# ==================== 构建状态图 ====================

graph_builder = StateGraph(
    state_schema=DataAgentState,
    context_schema=DataAgentContext
)

# 1. 注册可用节点
graph_builder.add_node("intent_detection", intent_detection)
graph_builder.add_node("direct_answer", direct_answer)
graph_builder.add_node("extract_keywords", extract_keywords)
graph_builder.add_node("column_recall", column_recall)
graph_builder.add_node("value_recall", value_recall)
graph_builder.add_node("metric_recall", metric_recall)
graph_builder.add_node("example_recall", example_recall)  # 🌟 注册金标召回节点
graph_builder.add_node("merge_retrieved_info", merge_retrieved_info)
graph_builder.add_node("resolve_table_relations", resolve_table_relations)
graph_builder.add_node("filter_table_info", filter_table_info)
graph_builder.add_node("add_context", add_context)
graph_builder.add_node("generate_sql", generate_sql)
graph_builder.add_node("validate_sql", validate_sql)
graph_builder.add_node("correct_sql", correct_sql)
graph_builder.add_node("translate_error", translate_error)
graph_builder.add_node("execute_sql", execute_sql)
graph_builder.add_node("present_result", present_result)

# 2. 编排边：起点 -> 意图判断
graph_builder.add_edge(START, "intent_detection")
graph_builder.add_conditional_edges(
    "intent_detection",
    route_intent,
    {
        "direct_answer": "direct_answer",
        "extract_keywords": "extract_keywords",
    }
)
# 闲聊直接回答后结束
graph_builder.add_edge("direct_answer", END)

# 3. 编排边：【四路】多路并发召回 (Fan-out) 与汇聚 (Fan-in)
graph_builder.add_edge("extract_keywords", "column_recall")
graph_builder.add_edge("extract_keywords", "value_recall")
graph_builder.add_edge("extract_keywords", "metric_recall")
graph_builder.add_edge("extract_keywords", "example_recall")  # 🌟 并发打入金标召回

graph_builder.add_edge("column_recall", "merge_retrieved_info")
graph_builder.add_edge("value_recall", "merge_retrieved_info")
graph_builder.add_edge("metric_recall", "merge_retrieved_info")
graph_builder.add_edge("example_recall", "merge_retrieved_info")  # 🌟 汇聚等待四路完成

# 4. 编排边：解析表关系 -> 过滤表 -> 装配上下文
graph_builder.add_edge("merge_retrieved_info", "resolve_table_relations")
graph_builder.add_edge("resolve_table_relations", "filter_table_info")
graph_builder.add_edge("filter_table_info", "add_context")

# 5. 编排边：生成 SQL 与 质检自愈环路
graph_builder.add_edge("add_context", "generate_sql")
graph_builder.add_edge("generate_sql", "validate_sql")

graph_builder.add_conditional_edges(
    "validate_sql",
    route_after_validation,
    {
        "execute_sql": "execute_sql",
        "correct_sql": "correct_sql",
        "translate_error": "translate_error",
    }
)

# 纠错完成后，再次回到质检节点复验（闭环回路）
graph_builder.add_edge("correct_sql", "validate_sql")
# 熔断报错翻译后结束
graph_builder.add_edge("translate_error", END)

# 6. 编排边：执行取数 -> 结果展示与协议推送 -> 抵达终点
graph_builder.add_edge("execute_sql", "present_result")
graph_builder.add_edge("present_result", END)

# 编译图
graph = graph_builder.compile()
