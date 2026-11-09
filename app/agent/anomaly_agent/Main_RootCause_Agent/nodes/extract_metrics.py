"""从用户问题中提取明确指标。"""

from __future__ import annotations

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langgraph.runtime import Runtime
from pydantic import BaseModel, ConfigDict, Field

from app.agent.anomaly_agent.Main_RootCause_Agent.state import RootCauseState
from app.agent.data_agent.context import DataAgentContext
from app.core.llm import get_context_llm
from app.prompt.prompt_loader import load_prompt


class MetricExtraction(BaseModel):
    model_config = ConfigDict(extra="ignore")
    metrics: list[str] = Field(default_factory=list)


async def extract_metrics(
    state: RootCauseState,
    runtime: Runtime[DataAgentContext],
) -> dict[str, object]:
    """LLM 提取明确指标；不提取周期、维度或完整归因计划。"""
    llm = get_context_llm(runtime, temperature=0.0, streaming=False)
    prompt = ChatPromptTemplate.from_template(load_prompt("anomaly/extract_metrics"))
    parser = JsonOutputParser(pydantic_object=MetricExtraction)
    extracted = await (prompt | llm | parser).ainvoke({"query": state["raw_question"]})
    parsed = MetricExtraction.model_validate(extracted)
    metrics = [metric.strip() for metric in parsed.metrics if metric.strip()]
    return {"extracted_metrics": metrics}
