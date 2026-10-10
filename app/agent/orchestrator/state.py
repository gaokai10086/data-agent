"""Orchestrator 的状态契约。

这里不保存 SQL、Schema 或归因明细，只保存跨 Agent 编排所需的最小信息。
各子 Agent 使用自己的 State，调用边界通过适配节点完成转换。
"""
from typing import Any, Literal, TypedDict


class OrchestratorState(TypedDict, total=False):
    query: str
    intent: Literal[
        "query_database",
        "anomaly_analysis",
        "direct_answer",
        "explain_knowledge",
    ]
    intent_reason: str
    keywords: list[str]
    agent_result: dict[str, Any]
    error: str | None
