"""BusinessDiagnosisAgent 节点：证据整理、受控验证和多轮收敛。"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate

from app.agent.anomaly_agent.BusinessDiagnosis_Agent.schemas import (
    BusinessDiagnosisInput, DiagnosisDecision, DiagnosisEvidence, DiagnosisFinding,
    DiagnosisHypothesis, DiagnosisObservation, DiagnosisResult, evidence_id,
)
from app.agent.anomaly_agent.BusinessDiagnosis_Agent.state import BusinessDiagnosisState
from app.agent.anomaly_agent.BusinessDiagnosis_Agent.tools import (
    DIAGNOSIS_TOOL_REGISTRY, ScopedMetricDecompositionError,
)
from app.core.llm import get_context_llm


def _base_meta(input_data: BusinessDiagnosisInput) -> dict[str, str]:
    overall = input_data.inspection_outcome.overall_result
    assert overall is not None
    return {"request_id": input_data.analysis_request.request_id, "metric_key": input_data.metric_context.metric_key, "target_period": overall.target_period, "comparison_period": overall.comparison_period}


def _make_evidence(kind: str, source: str, status: str, reconciled: bool, payload: dict[str, Any], input_data: BusinessDiagnosisInput, scope: list[str] | None = None) -> DiagnosisEvidence:
    return DiagnosisEvidence(evidence_id=evidence_id(kind, payload), kind=kind, source=source, status=status, reconciled=reconciled, payload=payload, scope=scope or [], **_base_meta(input_data))


def prepare_evidence(state: BusinessDiagnosisState) -> dict[str, object]:
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


def _legal_tool_names(state: BusinessDiagnosisState) -> list[str]:
    input_data = state["input"]
    names: list[str] = []
    implemented = {contract.name for contract in input_data.skill_strategy.tool_contracts if contract.implemented}
    # 空 capability 列表表示父流程没有授权该验证工具，不能默认开放。
    if not input_data.available_tool_names:
        return names
    implemented &= set(input_data.available_tool_names)
    if input_data.attribution_result and "scoped_metric_decomposition" in implemented and any(path.attributions for path in input_data.attribution_result.paths):
        names.append("scoped_metric_decomposition")
    return names


def _merge_hypotheses(existing: list[DiagnosisHypothesis], incoming: list[DiagnosisHypothesis]) -> list[DiagnosisHypothesis]:
    merged = {item.hypothesis_id: item for item in existing}
    merged.update({item.hypothesis_id: item for item in incoming})
    return list(merged.values())


def _fallback_scoped_decision(evidence: list[DiagnosisEvidence], decision: DiagnosisDecision) -> DiagnosisDecision | None:
    """有已对账维度证据但尚无范围拆解时，防止模型提前结束。

    成员和 evidence ID 均来自真实 AttributionPath；这里只是程序化安全兜底，
    不创建新的维度或业务值。
    """
    if any(item.kind == "scoped_metric_decomposition" for item in evidence):
        return None
    for item in evidence:
        if item.kind != "dimension_attribution" or not item.reconciled:
            continue
        members = item.payload.get("attributions") or []
        if not members:
            continue
        member = min(members, key=lambda value: float(value.get("change_amount", 0)))
        if member.get("group_value") is None:
            continue
        return decision.model_copy(update={
            "action": "execute_tool",
            "tool_name": "scoped_metric_decomposition",
            "tool_arguments": {
                "dimension_column": item.payload.get("dimension_column"),
                "member_value": member.get("group_value"),
                "parent_evidence_id": item.evidence_id,
            },
            "reason": "已有对账维度证据但尚无成员范围拆解，先验证最大下降成员的订单量与客单价",
        })
    return None


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


async def main_reason(state: BusinessDiagnosisState, runtime=None) -> dict[str, object]:
    evidence = list(state.get("evidence_items", []))
    input_data = state["input"]
    strategy = input_data.skill_strategy
    round_count = state.get("round_count", 0)
    if round_count >= strategy.max_analysis_rounds:
        return {"decision": DiagnosisDecision(action="finish", reason="达到最大诊断轮次"), "round_count": round_count, "stop_reason": "max_rounds"}
    if not evidence:
        return {"decision": DiagnosisDecision(action="finish", reason="没有可信证据"), "round_count": round_count + 1, "stop_reason": "no_credible_evidence"}
    legal_tools = _legal_tool_names(state)
    try:
        if runtime is None or not getattr(runtime, "context", None):
            raise RuntimeError("诊断 LLM 不可用")
        parser = JsonOutputParser(pydantic_object=DiagnosisDecision)
        prompt = ChatPromptTemplate.from_template(
            """你是受控业务诊断 Agent。只能引用已有 evidence ID 和真实 Observation。
如果要验证假设，选择已注册且合法的工具，并在 tool_arguments 中填写：
dimension_column、member_value、parent_evidence_id。三者必须来自同一条已对账的 dimension_attribution evidence，不能编造。
验证工具只能分析已验证父级成员的订单量与客单价，保留原始筛选和周期。
工具完成后，根据新证据把假设标记为 supported、contradicted 或 evidence_gap；统计因素不是业务因果。
预算：最大轮次={max_rounds}，已用={round_count}，查询预算={query_budget}，已执行 SQL={query_count}。
已有证据：{evidence}
已有 Observation：{observations}
已注册工具：{tools}
用户问题：{question}
{format_instructions}"""
        )
        llm = get_context_llm(runtime, temperature=0.0, streaming=False, max_retries=0)
        raw = await (prompt | llm | parser).ainvoke({
            "evidence": json.dumps([item.model_dump(mode="json") for item in evidence], ensure_ascii=False, default=str),
            "observations": json.dumps([item.model_dump(mode="json") for item in state.get("observations", [])], ensure_ascii=False, default=str),
            "tools": json.dumps(legal_tools, ensure_ascii=False), "question": input_data.raw_question,
            "max_rounds": strategy.max_analysis_rounds, "round_count": round_count, "query_budget": strategy.max_query_budget,
            "query_count": state.get("query_count", 0), "format_instructions": parser.get_format_instructions(),
        })
        decision = DiagnosisDecision.model_validate(raw)
        valid_ids = {item.evidence_id for item in evidence}
        scoped_ids = {item.evidence_id for item in evidence if item.kind == "scoped_metric_decomposition"}
        hypotheses = []
        for item in decision.hypotheses:
            supporting = [x for x in item.supporting_evidence_ids if x in valid_ids]
            status = item.status
            if status in {"supported", "contradicted"} and not (set(supporting) & scoped_ids):
                status = "evidence_gap"
            hypotheses.append(item.model_copy(update={"supporting_evidence_ids": supporting, "status": status, "executable": False}))
        decision = decision.model_copy(update={
            "findings": [item.model_copy(update={"evidence_ids": [x for x in item.evidence_ids if x in valid_ids]}) for item in decision.findings],
            "hypotheses": hypotheses,
        })
        fallback = _fallback_scoped_decision(evidence, decision) if "scoped_metric_decomposition" in legal_tools else None
        if fallback is not None and decision.action == "finish":
            decision = fallback
        if decision.action == "execute_tool" and decision.tool_name not in legal_tools:
            return {"decision": decision, "hypotheses": _merge_hypotheses(state.get("hypotheses", []), hypotheses), "round_count": round_count + 1, "stop_reason": "illegal_tool_request"}
        return {"decision": decision, "hypotheses": _merge_hypotheses(state.get("hypotheses", []), hypotheses), "round_count": round_count + 1, "stop_reason": "awaiting_tool" if decision.action == "execute_tool" else "completed"}
    except Exception as exc:
        observation = DiagnosisObservation(observation_id=uuid.uuid4().hex, action_name="main_reason", status="failed", reason="diagnosis_llm_failed", error=f"{type(exc).__name__}: {exc}", evidence_ids=[item.evidence_id for item in evidence])
        return {"decision": DiagnosisDecision(action="finish", reason="诊断模型调用失败，保留已验证证据"), "observations": list(state.get("observations", [])) + [observation], "round_count": round_count + 1, "stop_reason": "llm_failed"}


async def execute_diagnosis_tools(state: BusinessDiagnosisState, runtime=None) -> dict[str, object]:
    decision = state.get("decision")
    if decision is None or decision.action != "execute_tool":
        return {}
    if decision.tool_name not in DIAGNOSIS_TOOL_REGISTRY:
        observation = DiagnosisObservation(observation_id=uuid.uuid4().hex, action_name=decision.tool_name or "", status="blocked", reason="工具未注册或不在当前证据许可范围内", evidence_ids=[item.evidence_id for item in state.get("evidence_items", [])])
        return {"observations": list(state.get("observations", [])) + [observation], "stop_reason": "illegal_tool_request"}
    tool = DIAGNOSIS_TOOL_REGISTRY[decision.tool_name]
    args = decision.tool_arguments
    scoped_tasks = sum(item.kind == "scoped_metric_decomposition" for item in state.get("evidence_items", []))
    if scoped_tasks >= state["input"].skill_strategy.max_dimensions:
        observation = DiagnosisObservation(observation_id=uuid.uuid4().hex, action_name=decision.tool_name, status="blocked", reason="已达到最大验证维度数量", evidence_ids=[item.evidence_id for item in state.get("evidence_items", [])])
        return {"observations": list(state.get("observations", [])) + [observation], "stop_reason": "max_dimensions"}
    task_id = json.dumps(args, ensure_ascii=False, sort_keys=True)
    if task_id in state.get("executed_task_ids", []):
        observation = DiagnosisObservation(observation_id=uuid.uuid4().hex, action_name=decision.tool_name, status="blocked", reason="重复验证任务", evidence_ids=[item.evidence_id for item in state.get("evidence_items", [])])
        return {"observations": list(state.get("observations", [])) + [observation], "stop_reason": "duplicate_task"}
    remaining = state["input"].skill_strategy.max_query_budget - state.get("query_count", 0)
    try:
        payload = await tool.run(state["input"], runtime, tool_arguments=args, query_budget_remaining=remaining)
        payload_json = _jsonable(payload)
        payload_for_id = {key: value for key, value in payload_json.items() if key not in {"components", "reconciliation"}}
        evidence = DiagnosisEvidence(evidence_id=evidence_id("scoped_metric_decomposition", payload_for_id), kind="scoped_metric_decomposition", source="BusinessDiagnosisAgent.scoped_metric_decomposition", status="verified" if payload["reconciliation"].matches else "failed", reconciled=payload["reconciliation"].matches, scope=[f"{payload['dimension_column']}={payload['member_value']}"], payload=payload_json, **_base_meta(state["input"]))
        observation = DiagnosisObservation(observation_id=uuid.uuid4().hex, action_name=decision.tool_name, status="ok", reason="范围指标拆解完成并与当前渠道范围对账", evidence_ids=[evidence.evidence_id])
        return {"evidence_items": list(state.get("evidence_items", [])) + [evidence], "observations": list(state.get("observations", [])) + [observation], "query_count": state.get("query_count", 0) + payload["query_count"], "executed_task_ids": list(state.get("executed_task_ids", [])) + [task_id], "stop_reason": "tool_completed"}
    except asyncio.CancelledError:
        raise
    except ScopedMetricDecompositionError as exc:
        observation = DiagnosisObservation(observation_id=uuid.uuid4().hex, action_name=decision.tool_name, status="failed", reason="范围指标拆解失败", error=str(exc), evidence_ids=[item.evidence_id for item in state.get("evidence_items", [])])
        return {"observations": list(state.get("observations", [])) + [observation], "query_count": state.get("query_count", 0) + exc.query_count, "executed_task_ids": list(state.get("executed_task_ids", [])) + [task_id], "stop_reason": "tool_failed"}


def finalize_diagnosis(state: BusinessDiagnosisState) -> dict[str, object]:
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
