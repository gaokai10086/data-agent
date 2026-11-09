"""Main_RootCause_Agent 的状态契约。"""

from typing import TypedDict


class RootCauseState(TypedDict, total=False):
    """Skill 入口阶段的最小状态。"""

    # Orchestrator 已完成意图分流后传入的原始问题，保持原文不改写。
    raw_question: str
    # 从原问题提取的明确指标；允许为空表示未明确指标。
    extracted_metrics: list[str]
    # 匹配到的 Skill 名称；仅在 matched 状态写入。
    selected_skill_name: str
    # 匹配状态：matched / unsupported / ambiguous / missing_information。
    skill_match_status: str
    # 匹配结果的业务说明。
    skill_match_reason: str

