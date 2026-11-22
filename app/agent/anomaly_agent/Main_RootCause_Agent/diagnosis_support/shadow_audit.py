"""Read-only shadow decision for auditing, never wired into production routing."""
from __future__ import annotations

from decimal import Decimal

from app.agent.anomaly_agent.Main_RootCause_Agent.diagnosis_support.schemas import BusinessDiagnosisInput, evidence_id


def shadow_decision(input_data: BusinessDiagnosisInput, *, decline_coverage: Decimal = Decimal("0.80")) -> dict:
    """Select real members until cumulative gross decline coverage reaches threshold."""
    overall = input_data.inspection_outcome.overall_result
    assert overall is not None
    facts = [{"kind": "overall_comparison", "source": "ChangeInspection",
              "comparison_value": str(overall.comparison_value), "current_value": str(overall.current_value),
              "change_amount": str(overall.change_amount)}]
    gaps = []
    plans = []
    if input_data.attribution_result:
        for path in input_data.attribution_result.paths:
            rec = path.reconciliation
            if not (rec.comparison_matches and rec.current_matches and rec.change_matches):
                gaps.append({"kind": "unreconciled_attribution", "task_id": path.task_id})
                continue
            parent_id = evidence_id("dimension_attribution", path.model_dump(mode="json"))
            negative = sorted((member for member in path.attributions if member.change_amount < 0), key=lambda member: member.change_amount)
            denominator = sum((-member.change_amount for member in negative), Decimal(0))
            covered = Decimal(0)
            for member in negative:
                if denominator and covered / denominator >= decline_coverage:
                    break
                if 4 * (len(plans) + 1) > input_data.skill_strategy.max_query_budget:
                    gaps.append({"kind": "query_budget_exhausted", "task_id": path.task_id})
                    break
                covered += -member.change_amount
                plans.append({"tool_name": "scoped_metric_decomposition", "task_id": path.task_id,
                              "dimension_column": path.dimension_column, "member_value": member.group_value,
                              "parent_evidence_id": parent_id, "parent_scope": [item.model_dump(mode="json") for item in path.parent_scope],
                              "gross_decline_coverage": str(covered / denominator) if denominator else None,
                              "requires_sql": 4})
            facts.append({"kind": "dimension_attribution", "task_id": path.task_id, "evidence_id": parent_id,
                          "reconciled": True, "gross_decline_total": str(denominator)})
    else:
        gaps.append({"kind": "missing_attribution"})
    if input_data.decomposition_result and input_data.decomposition_result.reconciliation and input_data.decomposition_result.reconciliation.matches:
        facts.append({"kind": "metric_decomposition", "components": [item.model_dump(mode="json") for item in input_data.decomposition_result.components]})
    else:
        gaps.append({"kind": "missing_or_unreconciled_decomposition"})
    if not plans:
        gaps.append({"kind": "no_eligible_scoped_query"})
    hypotheses = [{"statement": f"{item['member_value']}的下降可能由订单量或客单价变化解释",
                   "status": "unverified", "required_plan": item["task_id"] + ":" + str(item["member_value"])} for item in plans]
    return {"facts_from_existing_evidence": facts, "evidence_gaps": gaps, "next_query_plan": plans,
            "unverified_hypotheses": hypotheses, "sql_executed": 0, "coverage_basis": "gross_decline_within_each_path"}
