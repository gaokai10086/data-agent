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
