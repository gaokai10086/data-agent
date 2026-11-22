"""Diagnostic entry point for saved evidence; executes the same Main Supervisor/actions."""
from __future__ import annotations

from app.agent.anomaly_agent.Main_RootCause_Agent.diagnosis_support.schemas import BusinessDiagnosisInput, DiagnosisResult
from app.agent.anomaly_agent.Main_RootCause_Agent.actions.registry import MAX_ACTION_EXECUTIONS
from app.agent.anomaly_agent.Main_RootCause_Agent.diagnosis import build_input, evidence_for
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.action_executor import _action_signature, action_executor
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.supervisor_reason import supervisor_reason
from app.agent.anomaly_agent.Main_RootCause_Agent.skills.registry import load_skill
from app.agent.anomaly_agent.Main_RootCause_Agent.budget import sql_usage


async def run_from_evidence(input_data: BusinessDiagnosisInput, runtime) -> dict:
    request = input_data.analysis_request
    observations = [{"step": 1, "action_name": "change_inspection", "status": "ready", "tool_called": True,
                     "dedupe_key": _action_signature("change_inspection", request),
                     "inspection_outcome": input_data.inspection_outcome.model_dump(mode="json")}]
    if input_data.attribution_result:
        observations.append({"step": len(observations) + 1, "action_name": "dimension_attribution", "status": "completed",
                             "completion_status": input_data.attribution_result.completion_status,
                             "attribution_result": input_data.attribution_result.model_dump(mode="json")})
    else:
        observations.append({"step": len(observations) + 1, "action_name": "dimension_attribution", "status": "unavailable",
                             "completion_status": "none", "reason": "saved_evidence_missing"})
    if input_data.decomposition_result:
        observations.append({"step": len(observations) + 1, "action_name": "metric_decomposition", "status": "completed",
                             "completion_status": input_data.decomposition_result.completion_status,
                             "decomposition_result": input_data.decomposition_result.model_dump(mode="json")})
    skill = load_skill("sales_amount_anomaly")
    state = {"raw_question": input_data.raw_question + "；进一步诊断已有证据及业务假设", "skill_result": {"status": "matched", "skill_name": skill.name},
             "schema_result": {"task_type": "hypothesis_analysis"},
             "parsed_result": {"status": "passed", "parsed_request": {
                 "metric": request.metric,
                 "target_period": {"year": int(request.target_period[:4]), "month": int(request.target_period[5:])},
                 "comparison_period": {"year": int(request.comparison_period[:4]), "month": int(request.comparison_period[5:])},
                 "filters": [item.model_dump(mode="json") for item in request.filters],
                 "expected_direction": request.expected_direction}},
             "observations": observations, "diagnosis_scoped_allowed": bool(input_data.available_tool_names),
             "sql_budget_limit": input_data.skill_strategy.max_query_budget}
    for _ in range(MAX_ACTION_EXECUTIONS + 1):
        update = await supervisor_reason(state, runtime)
        state.update(update)
        if (update.get("supervisor_decision") or {}).get("action") != "call_tool":
            break
        state.update(await action_executor(state, runtime))
    result = DiagnosisResult.model_validate(state["diagnosis_result"]) if state.get("diagnosis_result") else None
    refreshed = build_input(state["observations"], request, skill, input_data.raw_question,
                            allow_scoped=bool(input_data.available_tool_names))
    return {"main_state": state, "result": result, "evidence_items": evidence_for(state, refreshed),
            "query_count": (sql_usage(state["observations"])["by_action"].get("scoped_metric_decomposition") or 0),
            "round_count": state.get("step_count", 0)}
