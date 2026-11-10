"""销售额异动分析 Skill 的可执行实现。"""

from app.agent.anomaly_agent.Main_RootCause_Agent.skills.base import (
    AttributionToolContract,
    BaseSkill,
    SkillConfig,
    SkillStrategy,
)


class SalesAmountAnomalySkill(BaseSkill):
    """处理销售额指标异动和驱动因素分析。"""

    _METRIC_NAMES = ("销售额", "销售金额", "营业额", "销售收入", "gmv")

    @property
    def config(self) -> SkillConfig:
        return SkillConfig(
            name="sales_amount_anomaly",
            version="1.0",
            description="解释销售额变化，识别维度贡献并形成有证据的分析结果。",
            supported_intents=("anomaly_analysis",),
            supported_metrics=self._METRIC_NAMES,
            instruction_file="sales_amount_anomaly.md",
            capabilities=(
                "metric_change_analysis",
                "dimension_contribution_analysis",
                "metric_decomposition_analysis",
            ),
            constraints=(
                "只分析已通过前置变化核验的销售额请求",
                "只能使用已发布销售额指标定义和已验证的维度",
                "不能直接生成或执行未经校验的 SQL",
                "不能把相关性直接表述为因果关系",
            ),
            related_metrics=(),
            business_rules=(
                "销售额口径必须来自已发布的指标定义",
                "变化方向以已核验的周期总体值判断",
            ),
            analysis_guidance=(
                "先确认销售额变化方向，再按维度贡献与指标拆解逐层分析，"
                "所有结论必须引用查询证据。"
            ),
            allowed_capabilities=("change_inspection",),
            enabled=True,
            open_question_scenarios=(),
            task_types=(
                "period_comparison",
                "anomaly_location",
                "dimension_attribution",
                "metric_decomposition",
                "hypothesis_analysis",
            ),
            strategy=SkillStrategy(
                change_inspection_required=True,
                dimension_source="metric_context.dimensions",
                preserve_filters=True,
                ambiguity_action="reject",
                allow_dynamic_dimension_selection=True,
                max_analysis_rounds=3,
                max_dimensions=3,
                max_query_budget=8,
                max_depth=2,
                tool_contracts=(
                    AttributionToolContract(
                        name="dimension_group_query",
                        purpose="按一个已验证维度统计目标周期和对比周期的指标值",
                    ),
                    AttributionToolContract(
                        name="dimension_period_comparison",
                        purpose="比较维度在两个周期的指标变化",
                        implemented=True,
                    ),
                    AttributionToolContract(
                        name="contribution_calculation",
                        purpose="用确定性代码计算净变化贡献和贡献率",
                    ),
                    AttributionToolContract(
                        name="reconciliation_check",
                        purpose="校验维度分组变化额与总体变化额是否对账",
                    ),
                    AttributionToolContract(
                        name="drilldown_validation",
                        purpose="校验下钻维度、筛选继承和查询预算",
                    ),
                ),
                evidence_requirements=(
                    "每个维度必须有两个周期的真实指标值",
                    "贡献金额和贡献率必须由确定性代码计算",
                    "必须记录分组变化额与总体变化额的对账结果",
                    "必须区分净变化贡献与下降贡献",
                    "贡献关系不得直接表述为业务因果",
                ),
                termination_conditions=(
                    "ChangeInspection 非 ready",
                    "达到分析轮次、维度数量或查询预算上限",
                    "没有新的合法维度或证据不足",
                    "已分析相同维度和筛选范围",
                    "对账失败且无法安全解释",
                ),
            ),
        )

    def match_metric(self, metric: str) -> float:
        """根据解析出的指标名（含别名）计算匹配分数。"""

        normalized_metric = metric.strip().casefold()
        return 1.0 if any(name.casefold() == normalized_metric for name in self._METRIC_NAMES) else 0.0


SALES_AMOUNT_ANOMALY_SKILL = SalesAmountAnomalySkill()

