"""应用级意图识别，不依赖任何具体子 Agent 的 State。"""
from __future__ import annotations

from typing import Literal

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langgraph.runtime import Runtime
from pydantic import BaseModel, Field

from app.core.logging import logger
from app.agent.data_agent.context import DataAgentContext
from app.core.llm import get_context_llm
from app.agent.orchestrator.state import OrchestratorState
from app.prompt.prompt_loader import load_prompt


class IntentResult(BaseModel):
    intent: Literal[
        "query_database",
        "anomaly_analysis",
        "direct_answer",
        "explain_knowledge",
    ]
    reason: str = ""
    keywords: list[str] = Field(default_factory=list)


async def intent_detection(
    state: OrchestratorState,
    runtime: Runtime[DataAgentContext],
) -> dict[str, object]:
    """使用 LLM 识别应用意图，输出稳定的四分类标签。"""
    runtime.stream_writer({"stage": "意图识别"})
    query = state.get("query", "")
    llm = get_context_llm(runtime, temperature=0.1, streaming=False)

    try:
        prompt = ChatPromptTemplate.from_template(load_prompt("orchestrator/intent_detection"))
        parser = JsonOutputParser(pydantic_object=IntentResult)
        result = IntentResult.model_validate(
            await (prompt | llm | parser).ainvoke({"query": query})
        )
        runtime.stream_writer({
            "type": "intent_result",
            "intent": result.intent,
            "reason": result.reason,
            "keywords": result.keywords,
        })
        runtime.stream_writer({"stage": f"识别为 {result.intent}"})
        return {
            "intent": result.intent,
            "intent_reason": result.reason,
            "keywords": result.keywords,
        }
    except Exception as exc:
        # 分类失败时不能猜测为普通问数；错误路由可能产生看似可信的错误答案。
        logger.error("Orchestrator 意图识别失败，停止分流: {}", exc)
        raise
