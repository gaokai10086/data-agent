"""
意图识别节点：判断用户问题是否需要查询数据库
"""
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import get_llm
from app.agent.state import DataAgentState
from app.core.logging import logger
from app.prompt.prompt_loader import load_prompt


async def intent_detection(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """
    判断用户问题是否需要查询数据库

    返回：
        - {"intent": "query_database"}: 需要查数据（走原有多路召回与Text-to-SQL）
        - {"intent": "direct_answer"}: 不需要查数据（直接走LLM对话回答）
    """
    writer = runtime.stream_writer
    writer({"stage": "意图识别"})

    query = state["query"]

    # 修复 1: 补齐工厂函数必备的 provider 参数
    llm = get_llm(
        provider="deepseek",
        model_tier="flash",
        temperature=0.1,
        streaming=False
    )

    try:
        prompt_template = load_prompt("intent_detection")
        prompt = ChatPromptTemplate.from_template(prompt_template)

        chain = prompt | llm | StrOutputParser()
        response_text = await chain.ainvoke({"query": query})
        intent_label = response_text.strip().lower()

        logger.info(f"用户问题: {query} | 识别意图原文本: {intent_label}")

        # 修复 2: 规范分类判定，避免原地修改 state["intent"]，统一走 return
        if "query" in intent_label and "not" not in intent_label and "no" not in intent_label:
            writer({"stage": "识别为数仓查询需求，启动问数链路"})
            return {"intent": "query_database"}
        else:
            writer({"stage": "识别为通用意图，准备直接回答"})
            return {"intent": "direct_answer"}

    except Exception as e:
        logger.error(f"意图识别发生异常，降级默认走查库: {e}")
        # 修复 3: 异常兜底，优雅降级
        return {"intent": "query_database"}