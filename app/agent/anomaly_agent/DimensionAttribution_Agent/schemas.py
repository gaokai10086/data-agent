"""DimensionAttributionAgent 的输入、决策、观察和输出契约。"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent.anomaly_agent.schemas import (
    AnalysisRequest,
    InspectionOutcome,
    MetricContext,
)
from app.agent.anomaly_agent.Main_RootCause_Agent.skills.base import SkillStrategy


class AttributionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["execute_tool", "finish"]
    tool_name: str | None = None
    dimension_column: str | None = None
    parent_scope: list["DimensionScope"] = Field(default_factory=list)
    depth: int = Field(default=0, ge=0)
    task_id: str | None = None
    stop_reason: Literal[
        "completed", "budget_exhausted", "insufficient_evidence",
        "max_depth_reached", "no_candidate_dimensions", "decision_failed",
        "no_tools_available", "verification_not_ready", "execution_failed",
        "illegal_decision", "duplicate_task", "ambiguous_dimension",
        "duplicate_dimension",
    ] | None = None
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_action(self):
        if self.action == "execute_tool" and not self.tool_name:
            raise ValueError("execute_tool 必须指定 tool_name")
        if self.action == "finish" and (self.tool_name or self.dimension_column or self.parent_scope or self.task_id):
            raise ValueError("finish 不能携带工具或维度")
        return self


class DimensionScope(BaseModel):
    """已由上一轮真实 Observation 证明存在的父级成员范围。"""

    model_config = ConfigDict(extra="forbid")

    dimension_column: str = Field(min_length=1)
    member_value: str | None = None


class AttributionObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: str
    tool_name: str
    status: Literal["ok", "unavailable", "failed"]
    dimension_column: str | None = None
    task_id: str | None = None
    depth: int = 0
    parent_scope: list[DimensionScope] = Field(default_factory=list)
    query_count: int = 0
    evidence: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class DimensionMemberAttribution(BaseModel):
    group_value: str | None
    comparison_value: Decimal
    current_value: Decimal
    change_amount: Decimal
    change_rate: Decimal | None = None
    net_contribution_rate: Decimal | None = None


class ReconciliationResult(BaseModel):
    comparison_sum: Decimal
    current_sum: Decimal
    summed_change_amount: Decimal
    overall_comparison_value: Decimal
    overall_current_value: Decimal
    overall_change_amount: Decimal
    comparison_matches: bool
    current_matches: bool
    change_matches: bool
    tolerance: Decimal


class AttributionPath(BaseModel):
    """一轮真实维度查询及其对账证据。"""

    task_id: str
    depth: int
    dimension_column: str
    parent_scope: list[DimensionScope] = Field(default_factory=list)
    attributions: list[DimensionMemberAttribution] = Field(default_factory=list)
    reconciliation: ReconciliationResult
    sql_by_period: dict[str, str] = Field(default_factory=dict)
    informative: bool = True
    null_member_present: bool = False


class DimensionAttributionInput(BaseModel):
    """父 Agent 传给维度归因子 Agent 的已验证上下文。"""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    raw_question: str = Field(min_length=1)
    request: AnalysisRequest
    inspection_outcome: InspectionOutcome
    metric_context: MetricContext
    skill_name: str = Field(min_length=1)
    skill_version: str = Field(min_length=1)
    skill_strategy: SkillStrategy
    available_tool_names: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_ready_input(self):
        if self.inspection_outcome.status != "ready":
            raise ValueError("DimensionAttributionAgent 只能接收 ready 的 ChangeInspection 结果")
        if self.inspection_outcome.request_id != self.request.request_id:
            raise ValueError("请求与 InspectionOutcome 的 request_id 不一致")
        if self.inspection_outcome.metric_context is None:
            raise ValueError("ready 的 InspectionOutcome 必须包含 metric_context")
        if self.inspection_outcome.metric_context.metric_key != self.metric_context.metric_key:
            raise ValueError("请求指标与 InspectionOutcome 的 metric_key 不一致")
        return self


class AttributionResult(BaseModel):
    """归因结果；只有真实查询和对账成功时才返回 completed。"""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    status: Literal["completed", "unavailable", "failed"]
    completion_status: Literal["complete", "partial", "none"] = "none"
    reason: Literal[
        "no_tools_available",
        "verification_not_ready",
        "budget_exhausted",
        "completed",
        "execution_failed",
        "decision_failed",
        "illegal_decision",
        "insufficient_evidence",
        "duplicate_task",
        "max_depth_reached",
        "no_candidate_dimensions",
        "ambiguous_dimension",
        "duplicate_dimension",
    ]
    stop_reason: str | None = None
    analyzed_dimensions: list[str] = Field(default_factory=list)
    decisions: list[AttributionDecision] = Field(default_factory=list)
    observations: list[AttributionObservation] = Field(default_factory=list)
    summary: str
    total_query_count: int = 0
    attributions: list[DimensionMemberAttribution] = Field(default_factory=list)
    reconciliation: ReconciliationResult | None = None
    paths: list[AttributionPath] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unavailable_contract(self):
        if self.status == "unavailable" and self.reason not in {
            "no_tools_available", "verification_not_ready", "budget_exhausted",
            "decision_failed", "illegal_decision", "insufficient_evidence",
            "duplicate_task", "max_depth_reached", "no_candidate_dimensions",
            "ambiguous_dimension", "duplicate_dimension",
        }:
            raise ValueError("unavailable 必须使用未实现或预算终止原因")
        return self
