"""主 Supervisor 的证据推理输入；业务诊断不再运行独立子图。"""
from __future__ import annotations

import json
from typing import Any

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, ConfigDict, Field

from app.agent.anomaly_agent.Main_RootCause_Agent.diagnosis_support.nodes import prepare_evidence, finalize_diagnosis
from app.agent.anomaly_agent.Main_RootCause_Agent.diagnosis_support.audit import LLMUsage, enabled as audit_enabled
from app.agent.anomaly_agent.Main_RootCause_Agent.diagnosis_support.schemas import (
    BusinessDiagnosisInput, DiagnosisEvidence, DiagnosisHypothesis, DiagnosisResult,
)
from app.agent.anomaly_agent.Main_RootCause_Agent.diagnosis_support.shadow_audit import shadow_decision
from app.agent.anomaly_agent.DimensionAttribution_Agent.schemas import AttributionResult
from app.agent.anomaly_agent.MetricDecomposition_Agent.schemas import DecompositionResult
from app.agent.anomaly_agent.schemas import AnalysisRequest, InspectionOutcome
from app.core.llm import get_context_llm


class ScopedArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dimension_column: str = Field(min_length=1)
    member_value: str | None
    parent_evidence_id: str = Field(min_length=8)
    task_id: str = Field(min_length=1)


class DiagnosisChoice(BaseModel):
    model_config = ConfigDict(extra="ignore")
    action: str
    tool_arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    hypotheses: list[DiagnosisHypothesis] = Field(default_factory=list)


def build_input(observations: list[dict], request: AnalysisRequest, skill, question: str, *, allow_scoped: bool = True) -> BusinessDiagnosisInput:
    ready = next((item for item in reversed(observations) if item.get("action_name") == "change_inspection" and item.get("status") == "ready" and item.get("inspection_outcome")), None)
    if ready is None:
        raise ValueError("业务诊断需要同一请求的 ChangeInspection.ready")
    inspection = InspectionOutcome.model_validate(ready["inspection_outcome"])
    if inspection.request_id != request.request_id:
        request = request.model_copy(update={"request_id": inspection.request_id})
    attribution = next((AttributionResult.model_validate(item["attribution_result"]) for item in reversed(observations) if item.get("attribution_result")), None)
    decomposition = next((DecompositionResult.model_validate(item["decomposition_result"]) for item in reversed(observations) if item.get("decomposition_result")), None)
    return BusinessDiagnosisInput(
        raw_question=question, analysis_request=request, inspection_outcome=inspection,
        attribution_result=attribution, decomposition_result=decomposition,
        metric_context=inspection.metric_context, skill_strategy=skill.strategy,
        available_tool_names=("scoped_metric_decomposition",) if allow_scoped else (),
    )


def evidence_for(state: dict, input_data: BusinessDiagnosisInput) -> list[DiagnosisEvidence]:
    base = prepare_evidence({"input": input_data})["evidence_items"]
    extra = [DiagnosisEvidence.model_validate(item) for item in state.get("diagnosis_evidence", [])]
    ids = {item.evidence_id for item in base}
    for item in extra:
        if (item.request_id != input_data.analysis_request.request_id
                or item.metric_key != input_data.metric_context.metric_key
                or item.target_period != input_data.analysis_request.target_period
                or item.comparison_period != input_data.analysis_request.comparison_period):
            raise ValueError("范围证据与当前请求不一致")
        if item.evidence_id not in ids:
            base.append(item)
            ids.add(item.evidence_id)
    return base


def finalize(state: dict, input_data: BusinessDiagnosisInput, evidence: list[DiagnosisEvidence]) -> DiagnosisResult:
    hypotheses = [DiagnosisHypothesis.model_validate(item) for item in state.get("diagnosis_hypotheses", [])]
    failed = any(item.get("action_name") == "scoped_metric_decomposition" and item.get("status") in {"failed", "blocked"} for item in state.get("observations", []))
    result = finalize_diagnosis({"input": input_data, "evidence_items": evidence, "hypotheses": hypotheses,
                                 "observations": [], "stop_reason": state.get("stop_reason") or "completed",
                                 "query_count": sum((item.get("evidence") or {}).get("total_query_count", 0) for item in state.get("observations", []) if item.get("action_name") == "scoped_metric_decomposition")})["result"]
    if failed:
        result = result.model_copy(update={"status": "partial", "completion_status": "partial", "summary": "部分范围验证失败，已保留通过校验的证据。"})
    return result


async def reason(state: dict, runtime, input_data: BusinessDiagnosisInput, evidence: list[DiagnosisEvidence], *, remaining_sql: int) -> tuple[DiagnosisChoice, list[dict]]:
    plan = shadow_decision(input_data)["next_query_plan"]
    attempted: set[tuple[Any, ...]] = set()
    for observation in state.get("observations", []):
        if observation.get("action_name") != "scoped_metric_decomposition" or not observation.get("tool_arguments"):
            continue
        args = observation["tool_arguments"]
        attempted.add(tuple(args.get(key) for key in ("dimension_column", "member_value", "parent_evidence_id", "task_id")))
    candidates = [item for item in plan if tuple(item[key] for key in ("dimension_column", "member_value", "parent_evidence_id", "task_id")) not in attempted] if input_data.available_tool_names else []
    if remaining_sql < 4:
        candidates = []
    prompt = ChatPromptTemplate.from_template("""你是 Main RootCause Supervisor，负责判断业务证据是否充分。\n只能引用已有 evidence_id；统计贡献不是业务因果。若需要补查，只能从候选计划中选择一个，不能生成成员、维度或 SQL。\n用户问题：{question}\n已验证证据：{evidence}\n已知假设：{hypotheses}\n候选计划：{plans}\n返回 JSON：action 为 execute_tool 或 finish，tool_arguments 使用候选计划的 dimension_column、member_value、parent_evidence_id、task_id；并给出 reason 和 hypotheses。""")
    llm = get_context_llm(runtime, temperature=0.0, streaming=False, max_retries=0)
    raw = await (prompt | llm | JsonOutputParser(pydantic_object=DiagnosisChoice)).ainvoke({
        "question": input_data.raw_question,
        "evidence": json.dumps([item.model_dump(mode="json") for item in evidence], ensure_ascii=False),
        "hypotheses": json.dumps(state.get("diagnosis_hypotheses", []), ensure_ascii=False),
        "plans": json.dumps(candidates, ensure_ascii=False),
    }, config={"callbacks": [LLMUsage(getattr(llm, "model_name", None) or getattr(llm, "model", None))]} if audit_enabled() else None)
    choice = DiagnosisChoice.model_validate(raw)
    valid_ids = {item.evidence_id for item in evidence}
    scoped_ids = {item.evidence_id for item in evidence if item.kind == "scoped_metric_decomposition" and item.reconciled}
    cleaned = []
    for item in choice.hypotheses:
        refs = [value for value in item.supporting_evidence_ids if value in valid_ids]
        status = item.status
        if status in {"supported", "contradicted"} and not (set(refs) & scoped_ids):
            status = "evidence_gap"
        cleaned.append(item.model_copy(update={"supporting_evidence_ids": refs, "status": status, "executable": False}).model_dump(mode="json"))
    choice = choice.model_copy(update={"hypotheses": [DiagnosisHypothesis.model_validate(item) for item in cleaned]})
    # A finish before the configured coverage is reached would repeat the old
    # single-largest-member failure. Restrict the fallback to real candidates.
    if choice.action == "finish" and candidates:
        first = candidates[0]
        choice = choice.model_copy(update={"action": "execute_tool", "tool_arguments": {key: first[key] for key in ("dimension_column", "member_value", "parent_evidence_id", "task_id")},
                                           "reason": "累计下降覆盖未达到目标，验证下一个真实成员"})
    if choice.action == "execute_tool":
        args = ScopedArguments.model_validate(choice.tool_arguments)
        if not any(all(getattr(args, key) == item[key] for key in ("dimension_column", "member_value", "parent_evidence_id", "task_id")) for item in candidates):
            raise ValueError("LLM 选择了不存在的范围补查计划")
    elif choice.action != "finish":
        raise ValueError("未授权的业务诊断决策")
    return choice, cleaned
