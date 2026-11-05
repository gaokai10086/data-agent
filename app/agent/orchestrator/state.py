"""Orchestrator 的状态契约。

这里不保存 SQL、Schema 或归因明细，只保存跨 Agent 编排所需的最小信息。
各子 Agent 使用自己的 State，调用边界通过适配节点完成转换。
"""
from typing import Any, Literal, TypedDict


class OrchestratorState(TypedDict, total=False):
    # 用户原始问题，保持原样不覆盖，用于溯源与展示。
    query: str
    # 用于查询内存会话上下文的会话 ID。
    session_id: str | None
    # 改写节点输出的完整问题；意图识别与所有子 Agent 都消费它。
    rewritten_query: str

    # 三分类意图：问数 / 异动分析 / 闲聊直接回答。
    intent: Literal[
        "query_database",
        "anomaly_analysis",
        "direct_answer",
    ]
    # 被路由到的那个子 Agent 的最终结果；一次请求只有一个子 Agent 写它。
    agent_result: dict[str, Any]
