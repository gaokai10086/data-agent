"""销售额异动分析 Skill 的可执行实现。"""

from app.agent.anomaly_agent.Main_RootCause_Agent.skills.base import BaseSkill, SkillConfig


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
            allowed_capabilities=(),
            enabled=True,
            open_question_scenarios=(),
        )

    def match_metric(self, metric: str) -> float:
        """根据解析出的指标名（含别名）计算匹配分数。"""

        normalized_metric = metric.strip().casefold()
        return 1.0 if any(name.casefold() == normalized_metric for name in self._METRIC_NAMES) else 0.0


SALES_AMOUNT_ANOMALY_SKILL = SalesAmountAnomalySkill()

