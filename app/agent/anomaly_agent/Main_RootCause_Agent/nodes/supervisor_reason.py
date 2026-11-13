"""Supervisor Reason 节点：读取解析结果与 Observation，生成下一决策。"""

from __future__ import annotations

import json

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langgraph.runtime import Runtime
from pydantic import BaseModel, ConfigDict, Field

from app.agent.anomaly_agent.Main_RootCause_Agent.actions import ACTION_REGISTRY
from app.agent.anomaly_agent.Main_RootCause_Agent.skills.registry import (
    load_skill,
    resolve_skill_capabilities,
)
from app.agent.anomaly_agent.Main_RootCause_Agent.state import RootCauseState
from app.agent.data_agent.context import DataAgentContext
from app.core.llm import get_context_llm
from app.prompt.prompt_loader import load_prompt


class SupervisorDecisionModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: str
    tool_name: str | None = None
    reason: str = ""


def _observation_summary(observations: list[dict]) -> str:
    if not observations:
        return "[]"
    compact = [
        {
            "step": item.get("step"),
            "action": item.get("action_name"),
            "status": item.get("status"),
            "reason": item.get("reason"),
            "tool_called": item.get("tool_called"),
            "evidence": item.get("evidence"),
        }
        for item in observations
    ]
    return json.dumps(compact, ensure_ascii=False, default=str)


def _wants_decomposition(question: str, task_type: str | None) -> bool:
    return task_type == "metric_decomposition" or any(
        marker in question for marker in ("订单量", "客单价", "构成因素", "指标拆解")
    )


def _wants_dimension(question: str, task_type: str | None) -> bool:
    return task_type == "dimension_attribution" or any(
        marker in question for marker in ("渠道", "商品", "门店", "地区", "维度")
    )


def _wants_business_diagnosis(question: str, task_type: str | None) -> bool:
    return task_type == "hypothesis_analysis" or any(
        marker in question for marker in ("进一步诊断", "业务原因", "根因", "驱动因素", "业务驱动")
    )


def _specialist_preference(question: str, task_type: str | None) -> list[str]:
    """返回受限的专业 Action 顺序；顺序仅用于组合问题，不是 LLM 自由规划。"""
    decomposition = _wants_decomposition(question, task_type)
    dimension = _wants_dimension(question, task_type)
    business = _wants_business_diagnosis(question, task_type)
    if decomposition and dimension:
        dim_pos = min((question.find(x) for x in ("渠道", "商品", "门店", "地区", "维度") if x in question), default=10**9)
        dec_pos = min((question.find(x) for x in ("订单量", "客单价", "构成因素", "指标拆解") if x in question), default=10**9)
        ordered = ["dimension_attribution", "metric_decomposition"] if dim_pos <= dec_pos else ["metric_decomposition", "dimension_attribution"]
        return ordered + (["business_diagnosis"] if business else [])
    if decomposition:
        return ["metric_decomposition"] + (["business_diagnosis"] if business else [])
    if dimension:
        return ["dimension_attribution"] + (["business_diagnosis"] if business else [])
    if business:
        return ["business_diagnosis"]
    # “为什么下降”保留既有默认策略：先做维度贡献。
    return ["dimension_attribution"]


async def supervisor_reason(
    state: RootCauseState,
    runtime: Runtime[DataAgentContext],
) -> dict[str, object]:
    """每轮仅返回一个决策；不执行 Tool。"""
    skill_result = state.get("skill_result") or {}
    skill_name = skill_result.get("skill_name") or ""
    skill = load_skill(skill_name)
    capabilities = resolve_skill_capabilities(skill)
    allowed_actions = [
        name
        for name in ACTION_REGISTRY
        if ACTION_REGISTRY[name].capability in capabilities["available_capabilities"]
    ]

    observations = state.get("observations", [])
    latest = observations[-1] if observations else None
    if not observations:
        return {
            "supervisor_decision": {
                "action": "call_tool", "tool_name": "change_inspection",
                "reason": "必须先验证两个周期的实际变化",
            },
            "step_count": state.get("step_count", 0) + 1,
        }
    if latest and latest.get("action_name") == "change_inspection" and latest.get("status") == "failed":
        failures = sum(
            1 for item in observations
            if item.get("action_name") == "change_inspection" and item.get("status") == "failed"
        )
        if failures < 2 and "change_inspection" in allowed_actions:
            return {
                "supervisor_decision": {
                    "action": "call_tool", "tool_name": "change_inspection",
                    "reason": "变化检验技术失败，按有限重试协议重试一次",
                },
                "step_count": state.get("step_count", 0) + 1,
            }
        reason = "change_inspection_failed_retry_exhausted"
        return {
            "supervisor_decision": {"action": "stop", "tool_name": None, "reason": reason},
            "step_count": state.get("step_count", 0) + 1,
            "stop_reason": reason,
        }
    question = state.get("raw_question", "")
    task_type = (state.get("schema_result") or {}).get("task_type")
    preferences = _specialist_preference(question, task_type)
    if latest and latest.get("action_name") in {"dimension_attribution", "metric_decomposition", "business_diagnosis"}:
        status = latest.get("status") or "blocked"
        completion = latest.get("completion_status") or "none"
        child_stop = (
            latest.get("attribution_stop_reason")
            or latest.get("decomposition_stop_reason")
            or latest.get("business_diagnosis_stop_reason")
            or latest.get("reason")
        )
        if status in {"failed", "blocked"}:
            outcome_reason = f"{latest.get('action_name')}_{status}:{completion}:{child_stop}"
            return {
                "supervisor_decision": {"action": "stop", "tool_name": None, "reason": outcome_reason},
                "step_count": state.get("step_count", 0) + 1,
                "stop_reason": outcome_reason,
            }
        done_actions = {
            item.get("action_name") for item in observations
            if item.get("action_name") in {"dimension_attribution", "metric_decomposition", "business_diagnosis"}
            and item.get("status") in {"completed", "partial", "unavailable"}
        }
        next_action = next((name for name in preferences if name not in done_actions and name in allowed_actions), None)
        if next_action:
            return {
                "supervisor_decision": {"action": "call_tool", "tool_name": next_action,
                                        "reason": f"保留上一专业 Agent 证据，继续执行 {next_action}"},
                "step_count": state.get("step_count", 0) + 1,
            }
        outcome_reason = f"{latest.get('action_name')}_{status}:{completion}:{child_stop}"
        return {
            "supervisor_decision": {"action": "finish", "tool_name": None, "reason": outcome_reason},
            "step_count": state.get("step_count", 0) + 1,
            "stop_reason": outcome_reason,
        }
    if (
        latest and latest.get("action_name") == "change_inspection"
        and latest.get("status") == "ready"
        and latest.get("inspection_outcome")
    ):
        next_action = next((name for name in preferences if name in allowed_actions), None)
        if next_action:
            return {
                "supervisor_decision": {
                    "action": "call_tool", "tool_name": next_action,
                    "reason": f"变化检验 ready，委派已授权的 {next_action} Agent",
                },
                "step_count": state.get("step_count", 0) + 1,
            }
    if len(observations) >= 3:
        return {
            "supervisor_decision": {
                "action": "stop",
                "tool_name": None,
                "reason": "budget_exhausted",
            },
            "step_count": state.get("step_count", 0) + 1,
            "stop_reason": "budget_exhausted",
        }

    llm = get_context_llm(runtime, temperature=0.0, streaming=False)
    prompt = ChatPromptTemplate.from_template(load_prompt("anomaly/supervisor_reason"))
    parser = JsonOutputParser(pydantic_object=SupervisorDecisionModel)
    parsed = await (prompt | llm | parser).ainvoke({
        "allowed_actions": "\n".join(f"- {name}" for name in allowed_actions),
        "raw_question": state.get("raw_question", ""),
        "parsed_request": json.dumps(
            (state.get("parsed_result") or {}).get("parsed_request"),
            ensure_ascii=False,
            default=str,
        ),
        "observations": _observation_summary(observations),
    })
    decision = SupervisorDecisionModel.model_validate(parsed)

    if decision.action == "call_tool":
        if decision.tool_name not in allowed_actions:
            decision = SupervisorDecisionModel(
                action="stop",
                tool_name=None,
                reason=f"LLM 选择了未授权 Action：{decision.tool_name}",
            )

    return {
        "supervisor_decision": {
            "action": decision.action,
            "tool_name": decision.tool_name,
            "reason": decision.reason,
        },
        "step_count": state.get("step_count", 0) + 1,
    }
