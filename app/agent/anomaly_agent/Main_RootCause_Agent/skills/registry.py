"""Skill 注册、匹配和加载。"""

from dataclasses import dataclass
from typing import Literal

from app.agent.anomaly_agent.Main_RootCause_Agent.skills.SalesAmountAnomalySkill.sales_amount_anomaly import (
    SALES_AMOUNT_ANOMALY_SKILL,
)
from app.agent.anomaly_agent.Main_RootCause_Agent.skills.base import BaseSkill


class UnsupportedSkillError(ValueError):
    """没有可处理当前意图的 Skill。"""


SKILL_REGISTRY: dict[str, BaseSkill] = {
    SALES_AMOUNT_ANOMALY_SKILL.name: SALES_AMOUNT_ANOMALY_SKILL,
}

# 当前系统真实注册、可执行的能力。未出现在这里的名称不可被声明为可用。
AVAILABLE_CAPABILITIES = {
    "data_query",
    "change_inspection",
}


@dataclass(frozen=True)
class SkillMatchResult:
    status: Literal["matched", "unsupported", "ambiguous", "missing_information"]
    selected_skill_name: str | None = None
    reason: str = ""


def load_skill(skill_name: str) -> BaseSkill:
    """统一按 Skill 名称加载 Skill 实例。"""

    normalized_name = skill_name.strip()
    skill = SKILL_REGISTRY.get(normalized_name)
    if skill is None:
        raise UnsupportedSkillError(f"暂不支持 Skill：{skill_name}")
    if not skill.enabled:
        raise UnsupportedSkillError(f"Skill 未启用：{skill_name}")
    return skill


def get_skill_by_intent(intent: str) -> BaseSkill:
    """根据 Orchestrator 已确认的 intent 获取 Skill。

    这里不重复调用 LLM 判断意图，避免 Orchestrator 和子 Agent 得出
    不一致的分类结果。
    """

    normalized_intent = intent.strip()
    for skill in SKILL_REGISTRY.values():
        if not skill.enabled:
            continue
        if skill.supports_intent(normalized_intent):
            return skill
    raise UnsupportedSkillError(f"没有 Skill 可以处理意图：{intent}")


def get_skill_by_metric(metric: str, *, intent: str | None = None) -> BaseSkill:
    """根据解析出的指标选择 Skill，可用 intent 先缩小候选范围。"""

    normalized_metric = metric.strip()
    if not normalized_metric:
        raise ValueError("选择 Skill 的指标不能为空")

    candidates = [skill for skill in SKILL_REGISTRY.values() if skill.enabled]
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


def list_enabled_skills() -> list[BaseSkill]:
    """返回所有已启用的 Skill。"""
    return [skill for skill in SKILL_REGISTRY.values() if skill.enabled]


def resolve_skill_capabilities(skill: BaseSkill) -> dict[str, list[str]]:
    """核对 Skill 声明能力与系统真实注册能力。"""
    allowed = {name for name in skill.allowed_capabilities}
    available = sorted(allowed & AVAILABLE_CAPABILITIES)
    unavailable = sorted(allowed - AVAILABLE_CAPABILITIES)
    return {
        "allowed_capabilities": sorted(allowed),
        "available_capabilities": available,
        "unavailable_capabilities": unavailable,
    }


def validate_skill_strategy(skill: BaseSkill) -> None:
    """校验策略契约本身，不把未实现工具伪装成可执行能力。"""
    strategy = skill.strategy
    if strategy.change_inspection_required and "change_inspection" not in skill.allowed_capabilities:
        raise ValueError(f"Skill {skill.name} 必须声明 change_inspection 能力")
    if strategy.max_analysis_rounds < 1 or strategy.max_dimensions < 1:
        raise ValueError(f"Skill {skill.name} 的分析预算必须为正数")
    if strategy.max_query_budget < strategy.max_dimensions:
        raise ValueError(f"Skill {skill.name} 的查询预算不足以支持维度分析")
    if strategy.dimension_source != "metric_context.dimensions":
        raise ValueError(f"Skill {skill.name} 必须从 MetricContext/Meta 获取维度")


def match_skill(metrics: list[str], question: str) -> SkillMatchResult:
    """基于指标和原问题从已启用 Skill 中匹配唯一候选。"""
    normalized_metrics = [metric.strip() for metric in metrics if metric.strip()]
    candidates = list_enabled_skills()

    if not normalized_metrics:
        open_candidates = [
            skill
            for skill in candidates
            if any(marker in question for marker in skill.open_question_scenarios)
        ]
        if len(open_candidates) == 1:
            return SkillMatchResult("matched", open_candidates[0].name, "无明确指标，但 Skill 支持当前开放场景")
        if len(open_candidates) > 1:
            names = ", ".join(skill.name for skill in open_candidates)
            return SkillMatchResult("ambiguous", None, f"多个 Skill 支持开放场景：{names}")
        return SkillMatchResult(
            "missing_information",
            None,
            "问题未明确分析指标，且没有 Skill 声明支持该开放场景",
        )

    full_candidates = [
        skill
        for skill in candidates
        if all(skill.supports_metric(metric) for metric in normalized_metrics)
    ]
    if len(full_candidates) == 1:
        return SkillMatchResult("matched", full_candidates[0].name, "指标全部由同一 Skill 支持")
    if len(full_candidates) > 1:
        names = ", ".join(skill.name for skill in full_candidates)
        return SkillMatchResult("ambiguous", None, f"多个 Skill 支持相同指标：{names}")

    partial = [
        skill
        for skill in candidates
        if any(skill.supports_metric(metric) for metric in normalized_metrics)
    ]
    if partial:
        names = ", ".join(skill.name for skill in partial)
        return SkillMatchResult(
            "unsupported",
            None,
            f"存在部分指标不被任何单一 Skill 完整支持；候选：{names}",
        )
    return SkillMatchResult("unsupported", None, "没有已启用 Skill 支持当前指标")

