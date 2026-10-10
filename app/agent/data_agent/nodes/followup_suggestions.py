"""根据当前问数结果生成动态的下一步分析建议。"""
from __future__ import annotations

import json
from typing import Any, Dict

from langgraph.runtime import Runtime

from app.core.logging import logger
from app.agent.data_agent.context import DataAgentContext
from app.core.llm import get_context_llm
from app.agent.data_agent.state import DataAgentState

ALLOWED_TYPES = {"drilldown", "ranking", "comparison", "trend", "detail"}


def _fallback_actions(state: DataAgentState) -> list[dict[str, Any]]:
    query = state.get("query", "")
    analysis = state.get("result_analysis") or {}
    dimensions = analysis.get("dimensions") or []
    actions: list[dict[str, Any]] = []
    if dimensions:
        dimension = dimensions[0]
        actions.append({"id": "ranking", "type": "ranking", "label": "查看 Top 5",
                        "query": f"{query}，请只展示排名前5的数据", "reason": "当前结果存在可排序维度"})
        actions.append({"id": "drilldown", "type": "drilldown", "label": f"按{dimension}继续拆分",
                        "query": f"{query}，请进一步按{dimension}拆分", "reason": "当前结果包含业务维度"})
    actions.append({"id": "comparison", "type": "comparison", "label": "增加同比或环比",
                    "query": f"{query}，请增加同比和环比变化分析", "reason": "补充时间对比有助于判断趋势"})
    return actions[:3]


def _parse_json(text: str) -> list[dict[str, Any]]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    payload = json.loads(text[text.find("[") : text.rfind("]") + 1])
    if not isinstance(payload, list):
        raise ValueError("追问建议必须是 JSON 数组")
    return payload


def _build_prompt(state: DataAgentState) -> str:
    analysis = state.get("result_analysis") or {}
    insight = state.get("insight") or {}
    return f"""你是企业数据分析助手。请根据当前用户问题和查询结果，推荐 2-3 个最有价值的下一步分析动作。

用户问题：{state.get('query', '')}
结果结构：{json.dumps(analysis, ensure_ascii=False, default=str)}
业务洞察：{insight.get('summary', '')}

允许的动作类型：drilldown（下钻）、ranking（排名）、comparison（同比环比）、trend（趋势）、detail（明细）。
要求：
1. 建议必须和当前结果相关，不能凭空添加不存在的维度或指标。
2. 每条建议都必须包含可以直接提交给数据 Agent 的完整 query。
3. label 简短自然，不超过 12 个汉字。
4. 只输出 JSON 数组，不要 Markdown，不要解释。

格式：[
  {{"id":"ranking","type":"ranking","label":"查看 Top 5","query":"...","reason":"..."}}
]"""


async def followup_suggestions(
    state: DataAgentState,
    runtime: Runtime[DataAgentContext],
) -> Dict[str, Any]:
    writer = getattr(runtime, "stream_writer", None)
    if writer:
        writer({"stage": "生成后续分析建议"})

    if not state.get("sql_result") or not isinstance(state.get("sql_result"), list):
        return {"follow_up_actions": []}

    try:
        llm = get_context_llm(runtime, temperature=0.1)
        response = await llm.ainvoke(_build_prompt(state))
        candidates = _parse_json(response.content if hasattr(response, "content") else str(response))
        actions = []
        for index, item in enumerate(candidates[:3]):
            if not isinstance(item, dict):
                continue
            action_type = str(item.get("type", "")).lower()
            label = str(item.get("label", "")).strip()
            query = str(item.get("query", "")).strip()
            if action_type not in ALLOWED_TYPES or not label or not query:
                continue
            actions.append({"id": str(item.get("id") or f"action_{index}"),
                            "type": action_type, "label": label[:12], "query": query,
                            "reason": str(item.get("reason") or ""), "source": "llm"})
        if actions:
            logger.info("✨ [FollowUpSuggestions] LLM 生成 %s 条后续建议", len(actions))
            return {"follow_up_actions": actions}
    except Exception as exc:
        logger.warning("⚠️ [FollowUpSuggestions] LLM 生成失败，使用规则兜底：%s", exc)

    actions = _fallback_actions(state)
    for action in actions:
        action["source"] = "fallback"
    return {"follow_up_actions": actions}
