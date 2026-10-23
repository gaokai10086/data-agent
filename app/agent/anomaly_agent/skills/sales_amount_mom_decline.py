"""销售额月环比下降：先定位各维度的降幅贡献，不推断业务因果。"""
from datetime import date

from app.agent.anomaly_agent.schemas import (
    AnalysisContext, AnalysisStrategy, DimensionContributionStep,
)
from app.agent.anomaly_agent.skills.base import BaseAnalysisSkill, excluded_dimensions


class SalesAmountMomDeclineSkill(BaseAnalysisSkill):
    skill_id = "sales_amount_mom_decline"
    version = "1.1"

    def supports(self, context: AnalysisContext) -> bool:
        try:
            target = date.fromisoformat(context.request.target_period + "-01")
            comparison = date.fromisoformat(context.request.comparison_period + "-01")
        except ValueError:
            return False
        adjacent = (target.year * 12 + target.month) - (comparison.year * 12 + comparison.month) == 1
        return (
            context.metric.metric_key == "sales_amount"
            and context.metric.aggregation.upper() == "SUM"
            and context.request.comparison_type == "mom"
            and adjacent
            and context.verification.decision == "continue"
            and context.verification.actual_direction == "decrease"
        )

    async def build_strategy(self, context: AnalysisContext) -> AnalysisStrategy:
        if not self.supports(context):
            raise ValueError("当前请求不适用于销售额月环比下降 Skill")
        identity = dict(request_id=context.request.request_id,
                        skill_id=self.skill_id, skill_version=self.version)
        try:
            exclusions = excluded_dimensions(context)
        except ValueError as exc:
            return AnalysisStrategy(**identity, status="unavailable", reason="unresolved_filter",
                                    summary=str(exc))
        steps = []
        fixed = {item.dimension_column for item in exclusions}
        for dimension in context.metric.dimensions:
            if dimension.column in fixed:
                continue
            steps.append(DimensionContributionStep(
                step_id=f"dimension_{len(steps) + 1}",
                dimension_column=dimension.column,
                purpose=f"比较各{dimension.name}两个周期的销售额差额，定位主要降幅来源",
            ))
        if not steps:
            return AnalysisStrategy(**identity, status="unavailable", reason="no_candidate_dimensions",
                                    excluded_dimensions=exclusions,
                                    summary="当前指标没有可用于本 Skill 的未固定分析维度。")
        return AnalysisStrategy(**identity, status="ready", reason="skill_selected", steps=steps,
                                excluded_dimensions=exclusions,
                                summary="保留当前请求的周期和筛选范围，先进行维度贡献分析；计划尚未执行，不代表因果结论。")
