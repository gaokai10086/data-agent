"""Small structured handoff for a future Report Agent; heavy evidence stays in Observation."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agent.anomaly_agent.Main_RootCause_Agent.budget import sql_usage


class AnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = "1.0"
    request_id: str | None = None
    metric: str | None = None
    target_period: str | None = None
    comparison_period: str | None = None
    overall_verification: dict[str, Any] | None = None
    observation_refs: dict[str, list[int]] = Field(default_factory=dict)
    dimension_paths: list[dict[str, Any]] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    findings: list[dict[str, Any]] = Field(default_factory=list)
    hypotheses: list[dict[str, Any]] = Field(default_factory=list)
    stage_status: dict[str, str] = Field(default_factory=dict)
    stage_reasons: dict[str, str] = Field(default_factory=dict)
    sql_usage: dict[str, Any] = Field(default_factory=dict)
    stop_reason: str | None = None
    completion_status: Literal["complete", "partial", "none"] = "none"


def build_analysis_result(state: dict) -> AnalysisResult:
    observations = state.get("observations") or []
    refs: dict[str, list[int]] = {}
    stage_names = ("change_inspection", "dimension_attribution", "metric_decomposition", "business_diagnosis")
    statuses: dict[str, str] = {name: "pending" for name in stage_names}
    reasons: dict[str, str] = {}
    for index, item in enumerate(observations):
        name = item.get("action_name") or "unknown"
        refs.setdefault(name, []).append(index)
        statuses[name] = item.get("status") or "unknown"
        if item.get("reason"):
            reasons[name] = str(item["reason"])
    ready = next((item.get("inspection_outcome") for item in observations if item.get("action_name") == "change_inspection" and item.get("inspection_outcome")), None)
    overall = (ready or {}).get("overall_result") or {}
    metric = (ready or {}).get("metric_context") or {}
    paths = []
    for index in refs.get("dimension_attribution", []):
        for path in (observations[index].get("attribution_result") or {}).get("paths") or []:
            paths.append({"task_id": path.get("task_id"), "parent_task_id": path.get("parent_task_id"),
                          "depth": path.get("depth"), "observation_index": index,
                          "reconciled": all((path.get("reconciliation") or {}).get(key) is True for key in ("comparison_matches", "current_matches", "change_matches"))})
    diagnosis = state.get("diagnosis_result") or {}
    if diagnosis:
        statuses["business_diagnosis"] = diagnosis.get("status") or "partial"
        if diagnosis.get("stop_reason"):
            reasons["business_diagnosis"] = str(diagnosis["stop_reason"])
    observed_names = set(refs)
    if "metric_decomposition" not in observed_names:
        statuses["metric_decomposition"] = "skipped"
        reasons.setdefault("metric_decomposition", "not_scheduled")
    if "business_diagnosis" not in observed_names and not diagnosis:
        statuses["business_diagnosis"] = "skipped"
        reasons.setdefault("business_diagnosis", "not_requested_or_missing_scoped_evidence")
    evidence_ids = list(dict.fromkeys(diagnosis.get("evidence_ids") or [item.get("evidence_id") for item in state.get("diagnosis_evidence", []) if item.get("evidence_id")]))
    if not observations:
        completion = "none"
    elif any(item.get("status") in {"failed", "blocked", "partial", "unavailable"} or item.get("completion_status") in {"partial", "none"} for item in observations):
        completion = "partial"
    elif any(item.get("status") == "rejected" for item in observations):
        completion = "complete"
    else:
        completion = "complete"
    if diagnosis.get("completion_status") in {"partial", "none"}:
        completion = "partial"
    if state.get("stop_reason") in {"budget_exhausted", "sql_budget_exhausted", "diagnosis_reason_failed", "diagnosis_finalization_failed", "partial_completion"}:
        completion = "partial"
    return AnalysisResult(
        request_id=(ready or {}).get("request_id"), metric=metric.get("name"),
        target_period=overall.get("target_period"), comparison_period=overall.get("comparison_period"),
        overall_verification=(ready or {}).get("verification"), observation_refs=refs,
        dimension_paths=paths, evidence_ids=evidence_ids,
        findings=diagnosis.get("findings") or [], hypotheses=diagnosis.get("hypotheses") or [],
        stage_status=statuses, stage_reasons=reasons, sql_usage=sql_usage(observations),
        stop_reason=state.get("stop_reason"), completion_status=completion,
    )


def materialize_stage(state: dict, result: AnalysisResult, action_name: str) -> list[dict]:
    """Dereference typed stage payloads without parsing Supervisor prose."""
    observations = state.get("observations") or []
    return [observations[index] for index in result.observation_refs.get(action_name, [])]


def materialize_report_input(state: dict) -> dict[str, Any]:
    """Build a complete, read-only handoff without duplicating large payloads in Graph State."""
    result = AnalysisResult.model_validate(state.get("analysis_result") or build_analysis_result(state))
    inspection = materialize_stage(state, result, "change_inspection")
    return {
        "analysis_result": result.model_dump(mode="json"),
        "overall": (inspection[-1].get("inspection_outcome") or {}).get("overall_result") if inspection else None,
        "dimension_attribution": [item["attribution_result"] for item in materialize_stage(state, result, "dimension_attribution") if item.get("attribution_result")],
        "metric_decomposition": [item["decomposition_result"] for item in materialize_stage(state, result, "metric_decomposition") if item.get("decomposition_result")],
        "scoped_evidence": list(state.get("diagnosis_evidence") or []),
        "diagnosis": state.get("diagnosis_result"),
    }


def finalize_analysis(state: dict) -> dict[str, object]:
    update: dict[str, object] = {}
    observations = state.get("observations") or []
    if not state.get("diagnosis_result") and any(item.get("action_name") == "scoped_metric_decomposition" for item in observations):
        try:
            from app.agent.anomaly_agent.Main_RootCause_Agent.diagnosis import build_input, evidence_for, finalize
            from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.action_executor import _build_analysis_request
            from app.agent.anomaly_agent.Main_RootCause_Agent.skills.registry import load_skill
            request = _build_analysis_request((state.get("parsed_result") or {}).get("parsed_request") or {})
            skill = load_skill((state.get("skill_result") or {}).get("skill_name") or "")
            input_data = build_input(observations, request, skill, state.get("raw_question") or "异动分析")
            diagnosis = finalize(state, input_data, evidence_for(state, input_data))
            update["diagnosis_result"] = diagnosis.model_dump(mode="json")
        except (ValueError, KeyError, TypeError):
            # Keep the already verified Observations; the handoff remains partial.
            update["stop_reason"] = state.get("stop_reason") or "diagnosis_finalization_failed"
    merged = {**state, **update}
    update["analysis_result"] = build_analysis_result(merged).model_dump(mode="json")
    return update
