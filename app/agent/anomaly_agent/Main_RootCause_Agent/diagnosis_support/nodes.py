"""Reusable pure evidence assembly and finalization; no independent supervisor loop."""
from __future__ import annotations
from typing import Any
from app.agent.anomaly_agent.Main_RootCause_Agent.diagnosis_support.schemas import (BusinessDiagnosisInput, DiagnosisEvidence, DiagnosisFinding, DiagnosisResult, evidence_id)

def _base_meta(input_data: BusinessDiagnosisInput) -> dict[str, str]:
    overall = input_data.inspection_outcome.overall_result
    assert overall is not None
    return {"request_id": input_data.analysis_request.request_id, "metric_key": input_data.metric_context.metric_key, "target_period": overall.target_period, "comparison_period": overall.comparison_period}


def _make_evidence(kind: str, source: str, status: str, reconciled: bool, payload: dict[str, Any], input_data: BusinessDiagnosisInput, scope: list[str] | None = None) -> DiagnosisEvidence:
    return DiagnosisEvidence(evidence_id=evidence_id(kind, payload), kind=kind, source=source, status=status, reconciled=reconciled, payload=payload, scope=scope or [], **_base_meta(input_data))


def prepare_evidence(state: dict) -> dict[str, object]:
    input_data = BusinessDiagnosisInput.model_validate(state["input"])
    overall = input_data.inspection_outcome.overall_result
    assert overall is not None
    evidence: list[DiagnosisEvidence] = [_make_evidence(
        "overall_comparison", "ChangeInspection", "verified",
        bool(input_data.inspection_outcome.verification and input_data.inspection_outcome.verification.decision == "continue"),
        {"current_value": str(overall.current_value), "comparison_value": str(overall.comparison_value), "change_amount": str(overall.change_amount), "change_rate": str(overall.change_rate), "verification": input_data.inspection_outcome.verification.model_dump(mode="json") if input_data.inspection_outcome.verification else None}, input_data, ["overall"],
    )]
    if input_data.attribution_result is not None:
        for path in input_data.attribution_result.paths:
            reconciliation = path.reconciliation
            evidence.append(_make_evidence(
                "dimension_attribution", "DimensionAttributionAgent",
                "partial" if input_data.attribution_result.completion_status == "partial" else "verified" if reconciliation.comparison_matches and reconciliation.current_matches and reconciliation.change_matches else "failed",
                reconciliation.comparison_matches and reconciliation.current_matches and reconciliation.change_matches,
                path.model_dump(mode="json"), input_data, [path.dimension_column, f"depth={path.depth}"],
            ))
    if input_data.decomposition_result is not None:
        result = input_data.decomposition_result
        evidence.append(_make_evidence(
            "metric_decomposition", "MetricDecompositionAgent",
            "verified" if result.status == "completed" and result.reconciliation and result.reconciliation.matches else "partial" if result.components else "failed",
            bool(result.reconciliation and result.reconciliation.matches), result.model_dump(mode="json"), input_data,
            [item.factor_key for item in result.components],
        ))
    unique = {item.evidence_id: item for item in evidence}
    return {"input": input_data, "evidence_items": list(unique.values()), "observations": [], "hypotheses": [], "round_count": 0, "query_count": 0, "executed_task_ids": []}


def finalize_diagnosis(state: dict) -> dict[str, object]:
    input_data = state["input"]
    evidence = list(state.get("evidence_items", []))
    valid_ids = [item.evidence_id for item in evidence]
    findings: list[DiagnosisFinding] = []
    if not evidence:
        return {"result": DiagnosisResult(
            request_id=input_data.analysis_request.request_id,
            status="unavailable", completion_status="none", findings=[], hypotheses=state.get("hypotheses", []),
            evidence_ids=[], stop_reason=state.get("stop_reason") or "no_credible_evidence",
            summary="没有可信的已验证证据，未生成业务诊断。", total_query_count=state.get("query_count", 0),
        )}
    overall = input_data.inspection_outcome.overall_result
    if overall is not None and evidence:
        overall_id = next(item.evidence_id for item in evidence if item.kind == "overall_comparison")
        findings.append(DiagnosisFinding(finding_id="fact_overall_change", category="verified_fact", statement=f"{input_data.metric_context.name} 从 {overall.comparison_value} 变为 {overall.current_value}，变化额为 {overall.change_amount}。", evidence_ids=[overall_id]))
    for item in evidence:
        if item.kind == "dimension_attribution" and item.reconciled:
            members = item.payload.get("attributions") or []
            if members:
                largest = max(members, key=lambda value: abs(float(value.get("change_amount", 0))))
                findings.append(DiagnosisFinding(finding_id=f"finding_{item.evidence_id}", category="statistical_finding", statement=f"维度 {item.payload.get('dimension_column')} 中，成员 {largest.get('group_value')} 的统计变化额最大，为 {largest.get('change_amount')}；这表示统计贡献，不等同于业务因果。", evidence_ids=[item.evidence_id]))
        if item.kind in {"metric_decomposition", "scoped_metric_decomposition"} and item.reconciled:
            for component in item.payload.get("components") or []:
                name = component.get("factor_name")
                findings.append(DiagnosisFinding(finding_id=f"finding_{item.evidence_id}_{component.get('factor_key')}", category="statistical_finding", statement=f"范围 {item.scope} 的因素 {name} 对称分解贡献为 {component.get('contribution_amount')}。", evidence_ids=[item.evidence_id]))
    if not any(item.kind == "dimension_attribution" for item in evidence):
        findings.append(DiagnosisFinding(finding_id="gap_dimension", category="evidence_gap", statement="尚无已对账的维度归因证据。", evidence_ids=valid_ids))
    if not any(item.kind == "metric_decomposition" for item in evidence):
        findings.append(DiagnosisFinding(finding_id="gap_decomposition", category="evidence_gap", statement="尚无已对账的总体指标拆解证据。", evidence_ids=valid_ids))
    failed = (
        any(item.status in {"blocked", "failed"} for item in state.get("observations", []))
        or any(item.status == "partial" for item in evidence)
    )
    result = DiagnosisResult(request_id=input_data.analysis_request.request_id, status="partial" if failed else "completed", completion_status="partial" if failed else "complete", findings=findings, hypotheses=state.get("hypotheses", []), evidence_ids=valid_ids, stop_reason=state.get("stop_reason") or "completed", summary="已基于真实证据完成受控业务假设验证；统计贡献不等同于业务因果。", total_query_count=state.get("query_count", 0))
    return {"result": result}
