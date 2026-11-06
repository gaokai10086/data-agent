"""异动归因 Agent 的业务数据结构。

这里定义“解析出了什么”，不保存 LangGraph 的执行进度。
"""

from decimal import Decimal
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.meta.dimension_dependencies import validate_dimension_dependencies
from app.schemas.metric_decomposition import MetricDecomposition


class FilterCondition(BaseModel):
    """用户在问题中指定的筛选条件；值尚未与元数据核对。

    dimension 是 LLM 从原话里转写出的维度提示，只在用户明说时才有值；
    裸实体（如单独出现的“凯乐石”）dimension 为 None，交由值召回补全。
    值召回永远是物理列与合法值的最终权威，这里的 dimension 只是 hint。
    """

    dimension: str | None = None
    value: str
    operator: Literal["="] = "="


class ExtractedRequest(BaseModel):
    """LLM 从原话提取的字段；年月和筛选条件尚未完成业务校验。"""

    metric: str
    target_year: int
    target_month: int
    comparison_year: int | None = None
    comparison_month: int
    filters: list[FilterCondition] = Field(default_factory=list)
    expected_direction: Literal["decrease", "increase", "unspecified"] = "unspecified"


class AnalysisRequest(BaseModel):
    """程序校验后的比较请求，不代表数据库已证实用户所说的下降。

    metric 仍是用户的指标名称；是否对应合法指标由后续节点确认。
    """

    metric: str
    request_id: str = Field(default_factory=lambda: uuid4().hex)
    target_period: str
    comparison_period: str
    comparison_type: Literal["mom", "yoy", "custom"]
    filters: list[FilterCondition] = Field(default_factory=list)
    expected_direction: Literal["decrease", "increase", "unspecified"] = "unspecified"


class MetricDimension(BaseModel):
    """一个可用于分组或筛选的指标维度。

    name 是面向业务的名称，例如“门店”；column 是 Metadata 中确认过的
    物理字段，例如 ``dim_store.store_name``。异动 Agent 只消费这份契约，
    不直接读取 SQLAlchemy ORM 对象。
    """

    name: str
    column: str
    aliases: list[str] = Field(default_factory=list)
    determines: list[str] = Field(default_factory=list)


class MetricContext(BaseModel):
    """从共享 Metadata 解析出的指标上下文。

    这是 Metric Context 节点的输出，也是 Planner 后续制定 DataRequest 的
    输入。它描述“指标怎么算、能按什么分析”，不包含任何查询结果。
    """

    metric_key: str
    name: str
    description: str = ""
    formula: str = ""
    fact_table: str
    measure_column: str
    aggregation: str
    time_column: str | None = None
    filters: list[dict[str, Any]] = Field(default_factory=list)
    dimensions: list[MetricDimension] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    decomposition: MetricDecomposition | None = None

    @model_validator(mode="after")
    def check_dimension_dependencies(self):
        validate_dimension_dependencies([item.model_dump() for item in self.dimensions])
        return self


class BaseAnomalyResult(BaseModel):
    """所有异动分析结果共享的元数据。"""

    request_id: str
    metric_key: str
    schema_version: str = "1.0"


class OverallComparisonResult(BaseAnomalyResult):
    """一个指标在两个周期的总体值对比结果。"""

    analysis_type: Literal["overall_comparison"] = "overall_comparison"
    target_period: str
    comparison_period: str
    current_value: Decimal | None
    comparison_value: Decimal | None
    change_amount: Decimal | None = None
    change_rate: Decimal | None = None


# 兼容现有调用方；后续新增分析类型时统一使用具体结果类型。
OverallQueryResult = OverallComparisonResult


class AnalysisPlan(BaseModel):
    """异动分析路由计划。当前版本只实现总体对比。"""

    analysis_type: Literal["overall_comparison"] = "overall_comparison"


class ChangeVerification(BaseModel):
    """返回值与请求标识的变化核实；不代表 SQL 口径已验证或统计异常已检测。"""

    decision: Literal["continue", "stop"]
    reason: Literal[
        "change_confirmed", "direction_mismatch", "no_change", "insufficient_data",
        "result_mismatch", "invalid_period", "incomplete_period", "unsupported_result",
    ]
    expected_direction: Literal["decrease", "increase", "unspecified"]
    actual_direction: Literal["decrease", "increase", "unchanged", "unknown"] = "unknown"
    summary: str
    warnings: list[str] = Field(default_factory=list)


class PreparationResult(BaseModel):
    """前置核实阶段的统一输出，用于父图判断是否进入根因分析。

    status 表达“可以继续”“核实拒绝”“前置阶段本身失败”三种结局；
    reason 保留底层原因（可能是 ChangeVerification.reason，也可能是
    前置阶段的内部错误码），因此用自由字符串而非严格枚举。
    """

    status: Literal["ready", "rejected", "failed"]
    reason: str
    request_id: str | None = None
    user_message: str


class AnalysisContext(BaseModel):
    """Skill 的临时输入；组合已有事实，不重复写入 Graph State。"""

    request: AnalysisRequest
    metric: MetricContext
    comparison: OverallComparisonResult
    verification: ChangeVerification

    @model_validator(mode="after")
    def check_identity(self):
        request, result = self.request, self.comparison
        if (request.request_id != result.request_id
                or self.metric.metric_key != result.metric_key
                or request.target_period != result.target_period
                or request.comparison_period != result.comparison_period):
            raise ValueError("Skill 上下文的请求、指标或周期不一致")
        return self


class DimensionContributionStep(BaseModel):
    """维度贡献分析步骤；指标、周期、筛选统一继承当前请求。"""

    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(min_length=1)
    method: Literal["dimension_contribution"] = "dimension_contribution"
    dimension_column: str = Field(min_length=1)
    purpose: str = Field(min_length=1)


class ExcludedDimension(BaseModel):
    """本次计划排除的维度及其依据，不删除用户筛选。"""

    dimension_column: str
    reason: Literal["direct_filter", "dependency_fixed", "period_scope"]
    determined_by: str | None = None
    detail: str


class AnalysisStrategy(BaseModel):
    """各 Skill 统一的输出；unavailable 是正常业务结果，不执行空计划。"""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    status: Literal["ready", "unavailable"]
    reason: Literal[
        "skill_selected", "unsupported_skill", "ambiguous_skill",
        "no_candidate_dimensions", "unresolved_filter",
    ]
    skill_id: str | None = None
    skill_version: str | None = None
    steps: list[DimensionContributionStep] = Field(default_factory=list)
    excluded_dimensions: list[ExcludedDimension] = Field(default_factory=list)
    summary: str

    @model_validator(mode="after")
    def check_steps(self):
        if self.status == "ready":
            if not self.steps or not self.skill_id or not self.skill_version or self.reason != "skill_selected":
                raise ValueError("ready 策略必须提供 Skill、版本及分析步骤")
        elif self.steps or self.reason == "skill_selected":
            raise ValueError("unavailable 策略不能包含执行步骤或成功原因")
        if len({step.step_id for step in self.steps}) != len(self.steps):
            raise ValueError("分析步骤 ID 不能重复")
        if len({step.dimension_column for step in self.steps}) != len(self.steps):
            raise ValueError("分析维度不能重复")
        return self


class AnalysisScopeRef(BaseModel):
    """下钻只能引用已成功观察的真实分组，不接受自由编写的筛选。"""

    model_config = ConfigDict(extra="forbid")

    observation_id: str = Field(min_length=1)
    group_value: str


class AnalysisAction(BaseModel):
    """模型只能选择候选维度或结束，不能修改指标、周期、筛选或 SQL。"""

    model_config = ConfigDict(extra="forbid")

    action: Literal["dimension_contribution", "finish"]
    dimension_column: str | None = None
    scope_ref: AnalysisScopeRef | None = None
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def check_dimension(self):
        if self.action == "dimension_contribution" and not self.dimension_column:
            raise ValueError("维度分析动作必须指定维度")
        if self.action == "finish" and (self.dimension_column is not None or self.scope_ref is not None):
            raise ValueError("结束动作不能指定维度或下钻范围")
        return self


class GroupContribution(BaseModel):
    group_value: str | None
    current_value: Decimal
    comparison_value: Decimal
    change_amount: Decimal
    # 对当前查询范围净变化的有符号贡献；抵消存在时可为负数或超过 100%。
    contribution_rate: Decimal | None


class AnalysisObservation(BaseModel):
    """只有对账通过的结果才包含贡献明细；保留 SQL 以供人工复核口径。"""

    observation_id: str
    action: AnalysisAction
    status: Literal["ok", "error"]
    summary: str
    groups: list[GroupContribution] = Field(default_factory=list)
    sql_by_period: dict[str, str] = Field(default_factory=dict)
    scope_filters: list[FilterCondition] = Field(default_factory=list)
    scope_depth: int = 0
    baseline_current_value: Decimal | None = None
    baseline_comparison_value: Decimal | None = None
    baseline_change_amount: Decimal | None = None
    contribution_basis: Literal["query_scope_net_change"] = "query_scope_net_change"


class ChangeAnalysisReport(BaseModel):
    request_id: str
    stop_reason: Literal[
        "model_finished", "budget_exhausted", "no_candidates", "invalid_action",
        "decision_error", "timeout", "verification_stopped",
    ]
    decisions: list[AnalysisAction] = Field(default_factory=list)
    observations: list[AnalysisObservation] = Field(default_factory=list)
    summary: str


class InspectionOutcome(BaseModel):
    """可复用变化检验工具的统一返回契约。

    ready 表示总体变化前提得到支持，并不表示已完成归因、
    已验证 SQL 的真实业务口径，或已检验统计异常显著性。
    """

    status: Literal["ready", "rejected", "failed"]
    reason: str
    request_id: str | None = None
    user_message: str
    metric_context: MetricContext | None = None
    overall_result: OverallComparisonResult | None = None
    verification: ChangeVerification | None = None

    @model_validator(mode="after")
    def check_consistency(self):
        if self.status == "ready":
            if not all((self.request_id, self.metric_context, self.overall_result, self.verification)):
                raise ValueError("ready 核验结果必须提供请求标识、指标定义、总体结果和核验依据")
            if self.verification.decision != "continue":
                raise ValueError("ready 核验结果必须对应 continue 决策")
        elif self.verification is not None and self.verification.decision == "continue":
            raise ValueError("非 ready 状态不能携带 continue 核验结论")

        if self.overall_result is not None:
            if self.request_id != self.overall_result.request_id:
                raise ValueError("核验结果 request_id 与总体查询结果不一致")
            if self.metric_context is None or self.metric_context.metric_key != self.overall_result.metric_key:
                raise ValueError("总体查询结果的指标标识与 Metadata 不一致")
        return self
