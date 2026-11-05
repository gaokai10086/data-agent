"""Skill 注册、匹配和加载。"""

from app.agent.anomaly_agent.Main_RootCause_Agent.skills.SalesAmountAnomalySkill.sales_amount_anomaly import (
    SALES_AMOUNT_ANOMALY_SKILL,
)
from app.agent.anomaly_agent.Main_RootCause_Agent.skills.base import BaseSkill


class UnsupportedSkillError(ValueError):
    """没有可处理当前意图的 Skill。"""


SKILL_REGISTRY: dict[str, BaseSkill] = {
    SALES_AMOUNT_ANOMALY_SKILL.name: SALES_AMOUNT_ANOMALY_SKILL,
}


def load_skill(skill_name: str) -> BaseSkill:
    """统一按 Skill 名称加载 Skill 实例。"""

    normalized_name = skill_name.strip()
    skill = SKILL_REGISTRY.get(normalized_name)
    if skill is None:
        raise UnsupportedSkillError(f"暂不支持 Skill：{skill_name}")
    return skill


def get_skill_by_intent(intent: str) -> BaseSkill:
    """根据 Orchestrator 已确认的 intent 获取 Skill。

    这里不重复调用 LLM 判断意图，避免 Orchestrator 和子 Agent 得出
    不一致的分类结果。
    """

    normalized_intent = intent.strip()
    for skill in SKILL_REGISTRY.values():
        if skill.supports_intent(normalized_intent):
            return skill
    raise UnsupportedSkillError(f"没有 Skill 可以处理意图：{intent}")


def get_skill_by_metric(metric: str, *, intent: str | None = None) -> BaseSkill:
    """根据解析出的指标选择 Skill，可用 intent 先缩小候选范围。"""

    normalized_metric = metric.strip()
    if not normalized_metric:
        raise ValueError("选择 Skill 的指标不能为空")

    candidates = list(SKILL_REGISTRY.values())
    if intent is not None:
        candidates = [skill for skill in candidates if skill.supports_intent(intent)]

    scored = [(skill, skill.match_metric(normalized_metric)) for skill in candidates]
    matched = [(skill, score) for skill, score in scored if score > 0]
    if not matched:
        raise UnsupportedSkillError(f"没有 Skill 可以处理指标：{metric}")

    highest_score = max(score for _, score in matched)
    winners = [skill for skill, score in matched if score == highest_score]
    if len(winners) > 1:
        names = ", ".join(skill.name for skill in winners)
        raise UnsupportedSkillError(f"指标匹配到多个 Skill，无法确定：{names}")
    return winners[0]

