"""Orchestrator 的通用回答节点。"""
from __future__ import annotations

import re

from langchain_core.prompts import ChatPromptTemplate
from langgraph.runtime import Runtime

from app.agent.data_agent.context import DataAgentContext
from app.core.llm import get_context_llm
from app.agent.orchestrator.state import OrchestratorState
from app.prompt.prompt_loader import load_prompt


CAPABILITY_RESPONSE = """我是企业级数据分析助手，可以帮你：

- 查询业务数据：如销售额、订单量、用户数、转化率等
- 分析指标：同比、环比、趋势、占比、排名、异常波动
- 多维度拆解：按时间、地区、渠道、产品、客户等分析
- 解读报表：帮你理解图表、指标变化和可能原因
- 提供建议：SQL、Excel、Python、可视化及分析思路

如果你要查询具体数据，请告诉我：
1）**指标**：想看什么数据？
2）**时间范围**：如本月、近30天、今年
3）**分析维度**：如按地区、渠道、产品、客户分组"""

_CAPABILITY_QUERY_PATTERNS = (
    r"你(?:能|可以|会)做什么",
    r"你能帮我做什么",
    r"你可以帮我做什么",
    r"你有什么功能",
    r"你的能力(?:是什么|有哪些)?",
)


def is_capability_query(query: str) -> bool:
    """识别需要固定能力介绍的问法。

    这是一个有意保留的小范围确定性规则：能力介绍是产品固定文案，
    不应该因为模型采样而发生变化。它不参与业务意图识别，也不替代
    Orchestrator 的四分类路由。
    """
    text = re.sub(r"\s+", "", query or "")
    return any(re.search(pattern, text) for pattern in _CAPABILITY_QUERY_PATTERNS)


async def run_direct_answer(
    state: OrchestratorState,
    runtime: Runtime[DataAgentContext],
) -> dict[str, object]:
    runtime.stream_writer({"stage": "直接回复"})

    query = state.get("query", "")
    if is_capability_query(query):
        # 能力介绍使用固定模板，不再调用大模型，确保每次展示一致。
        runtime.stream_writer({"type": "token", "content": CAPABILITY_RESPONSE})
        return {"agent_result": {"final_answer": CAPABILITY_RESPONSE}}

    prompt = ChatPromptTemplate.from_template(load_prompt("orchestrator/direct_answer"))
    llm = get_context_llm(runtime, temperature=0.3, streaming=True)
    chunks: list[str] = []
    async for chunk in llm.astream(prompt.format_messages(query=query)):
        text = chunk.content if hasattr(chunk, "content") else str(chunk)
        if text:
            chunks.append(text)
            runtime.stream_writer({"type": "token", "content": text})
    return {"agent_result": {"final_answer": "".join(chunks)}}
