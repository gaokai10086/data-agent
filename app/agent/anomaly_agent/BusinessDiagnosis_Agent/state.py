"""BusinessDiagnosisAgent 的独立 LangGraph State。"""

from typing import TypedDict

from app.agent.anomaly_agent.BusinessDiagnosis_Agent.schemas import (
    BusinessDiagnosisInput,
    DiagnosisDecision,
    DiagnosisEvidence,
    DiagnosisHypothesis,
    DiagnosisObservation,
    DiagnosisResult,
)


class BusinessDiagnosisState(TypedDict, total=False):
    input: BusinessDiagnosisInput
    evidence_items: list[DiagnosisEvidence]
    hypotheses: list[DiagnosisHypothesis]
    decision: DiagnosisDecision
    observations: list[DiagnosisObservation]
    round_count: int
    query_count: int
    executed_task_ids: list[str]
    stop_reason: str
    result: DiagnosisResult
