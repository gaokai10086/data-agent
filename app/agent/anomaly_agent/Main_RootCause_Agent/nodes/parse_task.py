"""使用 LLM 将用户问题统一解析为 TaskRequest。"""

from __future__ import annotations

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langgraph.runtime import Runtime

from app.agent.anomaly_agent.Main_RootCause_Agent.state import RootCauseState
from app.agent.anomaly_agent.schemas import TaskRequest
from app.agent.data_agent.context import DataAgentContext
from app.core.llm import get_context_llm
from app.prompt.prompt_loader import load_prompt


async def parse_task(
    state: RootCauseState,
    runtime: Runtime[DataAgentContext],
) -> dict[str, object]:
    """LLM 结构化输出统一任务理解，不做数据库查询或归因。"""
    llm = get_context_llm(runtime, temperature=0.0, streaming=False)
    prompt = ChatPromptTemplate.from_template(load_prompt("anomaly/parse_task"))
    parser = JsonOutputParser(pydantic_object=TaskRequest)
    extracted = await (prompt | llm | parser).ainvoke({"query": state["raw_question"]})
    task_request = TaskRequest.model_validate(extracted)
    return {"task_request": task_request}
