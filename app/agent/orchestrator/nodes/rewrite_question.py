"""用 LLM 结合对话历史，把问题改写成可独立理解的完整问题（Query Rewrite）。"""
from __future__ import annotations

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langgraph.runtime import Runtime
from pydantic import BaseModel

from app.agent.data_agent.context import DataAgentContext
from app.agent.orchestrator import PROMPT_DIR
from app.agent.orchestrator.state import OrchestratorState
from app.core.llm import get_context_llm
from app.core.logging import logger
from app.prompt.prompt_loader import load_prompt
from app.service.conversation_context import conversation_history


class RewrittenQuestion(BaseModel):
    """改写节点的结构化输出。"""

    rewritten_query: str


async def rewrite_question(
    state: OrchestratorState,
    runtime: Runtime[DataAgentContext],
) -> dict[str, object]:
    """结合最近对话历史，把当前问题改写成可独立理解的完整问题。"""
    query = state.get("query", "")
    session_id = state.get("session_id")
    history = conversation_history.recent(session_id)

    # 没有历史就无需改写，直接透传，省一次模型调用。
    if not history:
        return {"rewritten_query": query}

    try:
        llm = get_context_llm(runtime, temperature=0.0, streaming=False)
        prompt = ChatPromptTemplate.from_template(
            load_prompt("rewrite_question", base_dir=PROMPT_DIR)
        )
        parser = JsonOutputParser(pydantic_object=RewrittenQuestion)
        result = RewrittenQuestion.model_validate(
            await (prompt | llm | parser).ainvoke({
                "history": "\n".join(history),
                "query": query,
            })
        )
        rewritten = result.rewritten_query.strip() or query
    except Exception as exc:
        # 改写是增强能力，失败时降级为原问题，不阻断主链路。
        logger.warning("Orchestrator 问题改写失败，使用原问题: %s", exc)
        rewritten = query

    if rewritten != query:
        runtime.stream_writer({
            "type": "query_rewritten",
            "original_query": query,
            "rewritten_query": rewritten,
            "session_id": session_id,
        })
    return {"rewritten_query": rewritten}
