"""业务洞察生成节点：先做事实计算，再调用 LLM 生成摘要。"""
from typing import Any, Dict

from langgraph.runtime import Runtime

from app.agent.data_agent.context import DataAgentContext
from app.core.llm import get_context_llm
from app.agent.data_agent.state import DataAgentState
from app.core.logging import logger
from app.agent.data_agent.progress import emit_node_result
from app.service.insight_service import InsightService


async def insight_generation(
    state: DataAgentState,
    runtime: Runtime[DataAgentContext],
) -> Dict[str, Any]:
    writer = getattr(runtime, "stream_writer", None)
    if writer:
        writer({"stage": "生成业务洞察"})

    rows = state.get("sql_result") or []
    if not isinstance(rows, list):
        rows = []
    analysis = state.get("result_analysis") or {}
    facts = InsightService.build_facts(rows, analysis)
    kpis = InsightService.build_kpis(rows, analysis)

    if not rows:
        insight = {
            "summary": "当前查询没有返回可分析的数据。",
            "facts": facts,
            "kpis": kpis,
            "source": "rule",
        }
        logger.info("✨ [InsightGeneration] 无数据，返回空洞察。")
        return {"insight": insight}

    prompt = InsightService.build_prompt(
        query=state.get("query", ""),
        analysis=analysis,
        facts=facts,
        rows=rows,
    )
    try:
        llm = get_context_llm(runtime, temperature=0.2)
        response = await llm.ainvoke(prompt)
        summary = (response.content if hasattr(response, "content") else str(response)).strip()
        source = "llm"
    except Exception as exc:
        logger.warning(f"⚠️ [InsightGeneration] LLM 生成失败，使用事实摘要兜底：{exc}")
        summary = "；".join(f"{fact['title']}：{fact['value']}" for fact in facts[:6])
        source = "fallback"

    insight = {"summary": summary, "facts": facts, "kpis": kpis, "source": source}
    logger.info(
        "✨ [InsightGeneration] 洞察生成完成: source={}, facts={}, kpis={}",
        source,
        len(facts),
        len(kpis),
    )
    logger.debug("[InsightGeneration] insight_summary={}", summary)
    emit_node_result(runtime, "业务洞察生成完成")
    return {"insight": insight}
