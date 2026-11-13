"""MetricDecompositionAgent 的输入、观察和结果契约。"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent.anomaly_agent.schemas import AnalysisRequest, InspectionOutcome, MetricContext
from app.schemas.metric_decomposition import MetricDecomposition
from app.agent.anomaly_agent.Main_RootCause_Agent.skills.base import SkillStrategy


class MetricDecompositionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    raw_question: str = Field(min_length=1)
    request: AnalysisRequest
    inspection_outcome: InspectionOutcome
    metric_context: MetricContext
    skill_name: str = Field(min_length=1)
    skill_version: str = Field(min_length=1)
    skill_strategy: SkillStrategy

    @model_validator(mode="after")
    def validate_ready_and_published_decomposition(self):
        if self.inspection_outcome.status != "ready":
            raise ValueError("MetricDecompositionAgent 只能接收 ready 的 ChangeInspection 结果")
        if self.inspection_outcome.request_id != self.request.request_id:
            raise ValueError("请求与 InspectionOutcome 的 request_id 不一致")
        if self.inspection_outcome.metric_context is None:
            raise ValueError("ready 结果必须包含 metric_context")
        if self.inspection_outcome.metric_context.metric_key != self.metric_context.metric_key:
            raise ValueError("指标上下文的 metric_key 不一致")
        return self


class FactorPeriodValue(BaseModel):
    factor_key: str
    metric_key: str
    metric_name: str
    target_value: Decimal
    comparison_value: Decimal
    sql_by_period: dict[str, str] = Field(default_factory=dict)


class DecompositionObservation(BaseModel):
    factor_key: str
    metric_key: str
    status: Literal["ok", "failed"]
    values: FactorPeriodValue | None = None
    error: str | None = None


class DecompositionComponent(BaseModel):
    factor_key: str
    factor_name: str
    comparison_value: Decimal
    current_value: Decimal
    change_amount: Decimal
    contribution_amount: Decimal
    contribution_rate: Decimal | None


class DecompositionReconciliation(BaseModel):
    overall_comparison_value: Decimal
    overall_current_value: Decimal
    overall_change_amount: Decimal
    summed_contribution_amount: Decimal
    matches: bool
    tolerance: Decimal


class DecompositionResult(BaseModel):
    request_id: str
    status: Literal["completed", "unavailable", "failed"]
    completion_status: Literal["complete", "partial", "none"] = "none"
    reason: Literal[
        "completed", "verification_not_ready", "formula_unavailable",
        "formula_mismatch", "dependency_unavailable", "execution_failed",
        "zero_denominator", "reconciliation_failed",
    ]
    stop_reason: str | None = None
    formula: MetricDecomposition | None = None
    observations: list[DecompositionObservation] = Field(default_factory=list)
    components: list[DecompositionComponent] = Field(default_factory=list)
    reconciliation: DecompositionReconciliation | None = None
    summary: str

