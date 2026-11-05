"""Main_RootCause_Agent 的状态契约。

第一阶段只定义跨节点必须保留的用户问题。后续解析、指标上下文和
根因分析结果都应作为独立字段逐步加入，而不是在接收节点中提前推断。
"""

from typing import Literal, TypedDict

from app.agent.anomaly_agent.schemas import AnalysisRequest


class RootCauseState(TypedDict, total=False):
    """Main_RootCause_Agent 的最小输入状态。"""

    # Orchestrator 已完成意图分流后传入的原始问题，保持原文不改写。
    raw_question: str
    # Orchestrator 已完成的意图识别结果
    intent: Literal["anomaly_analysis"]
    # 主 Agent 完成问题理解后写回的结构化请求，供 Skill 选择与后续调度使用。
    analysis_request: AnalysisRequest
    #  加载的skill名称
    selected_skill_name: str

