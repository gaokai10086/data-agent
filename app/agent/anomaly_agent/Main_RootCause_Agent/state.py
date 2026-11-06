"""Main_RootCause_Agent 的状态契约。

第一阶段只保留“接收 → 解析 → 选 Skill”链路必需的数据。
变化检验、归因、预算和报告字段在尚未接入对应节点前不提前声明。
"""

from typing import TypedDict

from app.agent.anomaly_agent.schemas import TaskRequest


class RootCauseState(TypedDict, total=False):
    """Main_RootCause_Agent 的最小输入状态。"""

    # Orchestrator 已完成意图分流后传入的原始问题，保持原文不改写。
    raw_question: str
    # 主 Agent 完成统一任务理解后写回的结构化请求。
    task_request: TaskRequest
    # 异动分析业务域内匹配到的 Skill 名称。
    selected_skill_name: str

