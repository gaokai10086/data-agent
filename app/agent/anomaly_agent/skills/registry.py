"""Skill 注册与唯一匹配；没有命中时不套用其他指标的方法。"""
from collections.abc import Sequence

from app.agent.anomaly_agent.schemas import AnalysisContext, AnalysisStrategy
from app.agent.anomaly_agent.skills.base import BaseAnalysisSkill, validate_strategy
from app.agent.anomaly_agent.skills.sales_amount_mom_decline import SalesAmountMomDeclineSkill


ANALYSIS_SKILLS: tuple[BaseAnalysisSkill, ...] = (SalesAmountMomDeclineSkill(),)


async def build_analysis_strategy(
    context: AnalysisContext,
    skills: Sequence[BaseAnalysisSkill] | None = None,
) -> AnalysisStrategy:
    if context.verification.decision != "continue":
        raise ValueError("变化核实未通过，不能选择分析 Skill")
    matches = [skill for skill in (ANALYSIS_SKILLS if skills is None else skills)
               if skill.supports(context)]
    if len(matches) != 1:
        return AnalysisStrategy(
            request_id=context.request.request_id, status="unavailable",
            reason="unsupported_skill" if not matches else "ambiguous_skill",
            summary="暂无适用的分析 Skill。" if not matches else "多个分析 Skill 同时匹配，请检查注册规则。",
        )
    skill = matches[0]
    strategy = await skill.build_strategy(context)
    if strategy.skill_id != skill.skill_id or strategy.skill_version != skill.version:
        raise ValueError("策略的 Skill 标识或版本与选中的 Skill 不一致")
    validate_strategy(context, strategy)
    return strategy
