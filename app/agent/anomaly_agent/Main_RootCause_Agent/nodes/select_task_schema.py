"""根据问题选择当前 Skill 已注册的单一任务 Schema。"""

from __future__ import annotations

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langgraph.runtime import Runtime
from pydantic import BaseModel, ConfigDict, Field

from app.agent.anomaly_agent.Main_RootCause_Agent.skills.registry import load_skill
from app.agent.anomaly_agent.Main_RootCause_Agent.state import RootCauseState
from app.agent.anomaly_agent.Main_RootCause_Agent.schemas import (
    TASK_SCHEMA_REGISTRY,
    get_task_schema,
)
from app.agent.data_agent.context import DataAgentContext
from app.core.llm import get_context_llm
from app.prompt.prompt_loader import load_prompt


class SchemaSelection(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: str
    task_type: str | None = None
    reason: str = ""


TASK_TYPE_DESCRIPTIONS = {
    "period_comparison": "用户明确比较两个周期的指标值或变化幅度",
    "anomaly_location": "用户关注异常发生在哪些城市、渠道或维度，通常使用相对时间",
    "dimension_attribution": "用户要求解释某个维度或渠道对指标变化的贡献",
    "metric_decomposition": "用户要求把指标拆解成订单量、客单价等构成因子",
    "hypothesis_analysis": "用户提出一个待验证的业务假设或原因猜想",
}


async def select_task_schema(
    state: RootCauseState,
    runtime: Runtime[DataAgentContext],
) -> dict[str, object]:
    """LLM 从 Skill 已注册任务类型中选择一个；不在此生成业务参数。"""
    skill_result = state.get("skill_result") or {}
    skill_name = skill_result.get("skill_name") or ""
    skill = load_skill(skill_name)
    task_types = [task_type for task_type in skill.task_types if task_type in TASK_SCHEMA_REGISTRY]
    if not task_types:
        return {
            "schema_result": {
                "status": "unsupported",
                "reason": "当前 Skill 未注册可用任务 Schema",
            },
        }

    llm = get_context_llm(runtime, temperature=0.0, streaming=False)
    prompt = ChatPromptTemplate.from_template(load_prompt("anomaly/select_task_schema"))
    parser = JsonOutputParser(pydantic_object=SchemaSelection)
    selected = await (prompt | llm | parser).ainvoke({
        "task_types": "\n".join(
            f"- {name}：{TASK_TYPE_DESCRIPTIONS.get(name, '')}" for name in task_types
        ),
        "query": state["raw_question"],
    })
    result = SchemaSelection.model_validate(selected)

    if result.status == "matched":
        if result.task_type not in task_types:
            return {
                "schema_result": {
                    "status": "unsupported",
                    "reason": f"LLM 返回了未注册任务类型：{result.task_type}",
                },
            }
        schema = get_task_schema(result.task_type)
        return {
            "schema_result": {
                "status": "matched",
                "reason": result.reason,
                "task_type": result.task_type,
                "schema_name": schema.__name__,
            },
        }

    return {
        "schema_result": {
            "status": result.status,
            "reason": result.reason,
        },
    }
