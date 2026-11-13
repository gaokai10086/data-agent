"""BusinessDiagnosisAgent 的输入、证据、决策和结果契约。"""

from __future__ import annotations

import json
from decimal import Decimal
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent.anomaly_agent.DimensionAttribution_Agent.schemas import AttributionResult
from app.agent.anomaly_agent.Main_RootCause_Agent.skills.base import SkillStrategy
from app.agent.anomaly_agent.MetricDecomposition_Agent.schemas import DecompositionResult
from app.agent.anomaly_agent.schemas import AnalysisRequest, InspectionOutcome, MetricContext


class BusinessDiagnosisInput(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    raw_question: str = Field(min_length=1)
    analysis_request: AnalysisRequest
    inspection_outcome: InspectionOutcome
    attribution_result: AttributionResult | None = None
    decomposition_result: DecompositionResult | None = None
    metric_context: MetricContext
    skill_strategy: SkillStrategy
    available_tool_names: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_trusted_context(self):
        outcome = self.inspection_outcome
        request = self.analysis_request
        if outcome.status != "ready":
            raise ValueError("BusinessDiagnosisAgent 只能接收 ready 的 ChangeInspection 结果")
        if outcome.request_id != request.request_id:
            raise ValueError("InspectionOutcome 与 AnalysisRequest 的 request_id 不一致")
        if outcome.metric_context is None or outcome.metric_context.metric_key != self.metric_context.metric_key:
            raise ValueError("InspectionOutcome 与 MetricContext 的指标不一致")
        overall = outcome.overall_result
        if overall is None:
            raise ValueError("ready 的 InspectionOutcome 必须包含总体比较结果")
        if (
            overall.request_id != request.request_id
            or overall.metric_key != self.metric_context.metric_key
            or overall.target_period != request.target_period
            or overall.comparison_period != request.comparison_period
        ):
            raise ValueError("总体变化证据与当前请求的指标或周期不一致")
        for name, result in (("attribution_result", self.attribution_result), ("decomposition_result", self.decomposition_result)):
            if result is not None and result.request_id != request.request_id:
                raise ValueError(f"{name} 与 AnalysisRequest 的 request_id 不一致")
        return self


class DiagnosisEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=8)
    kind: Literal["overall_comparison", "dimension_attribution", "metric_decomposition", "scoped_metric_decomposition"]
    source: str = Field(min_length=1)
    request_id: str
    metric_key: str
    target_period: str
    comparison_period: str
    status: Literal["verified", "partial", "failed"]
    reconciled: bool = False
    scope: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)


class DiagnosisFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_id: str = Field(min_length=1)
    category: Literal["verified_fact", "statistical_finding", "evidence_gap"]
    statement: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)


class DiagnosisHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypothesis_id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    status: Literal["hypothesis", "supported", "contradicted", "evidence_gap"] = "hypothesis"
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    validation_metrics: list[str] = Field(default_factory=list)
    executable: bool = False


class DiagnosisDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["finish", "execute_tool"]
    findings: list[DiagnosisFinding] = Field(default_factory=list)
    hypotheses: list[DiagnosisHypothesis] = Field(default_factory=list)
    tool_name: str | None = None
    tool_arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_tool_contract(self):
        if self.action == "execute_tool" and not self.tool_name:
            raise ValueError("execute_tool 必须指定 tool_name")
        if self.action == "finish" and self.tool_name:
            raise ValueError("finish 不能携带 tool_name")
        if self.action == "finish" and self.tool_arguments:
            raise ValueError("finish 不能携带 tool_arguments")
        return self


class DiagnosisObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: str
    action_name: str
    status: Literal["blocked", "failed", "ok"]
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)
    error: str | None = None


class DiagnosisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    status: Literal["completed", "partial", "unavailable", "failed"]
    completion_status: Literal["complete", "partial", "none"]
    findings: list[DiagnosisFinding] = Field(default_factory=list)
    hypotheses: list[DiagnosisHypothesis] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    stop_reason: str
    summary: str
    total_query_count: int = 0


def evidence_id(kind: str, payload: dict[str, Any]) -> str:
    """对规范化证据生成稳定 ID，避免重复引用同一真实结果。"""
    normalized = json.dumps({"kind": kind, "payload": payload}, ensure_ascii=False, sort_keys=True, default=str)
    return f"ev_{sha256(normalized.encode('utf-8')).hexdigest()[:16]}"
