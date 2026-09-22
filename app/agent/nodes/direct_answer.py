"""
直接回答节点：用大模型直接回答用户问题（不查询数据库）
"""
from langchain_core.prompts import ChatPromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import get_llm
from app.agent.state import DataAgentState
from app.core.logging import logger
from app.prompt.prompt_loader import load_prompt


async def direct_answer(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """
    大模型直接回答用户问题（不涉及数据库查询）
    """
    # 1. 初始化流式状态写入器
    writer = getattr(runtime, "stream_writer", None)
    if writer:
        writer({"stage": "直接回复"})

    query = state.get("query", "")

    # 2. 获取支持流式输出的模型实例
    llm = get_llm(
        provider="deepseek",
        model_tier="flash",
        temperature=0.3,
        streaming=True
    )

    try:
        # 3. 加载外置提示词并构建 ChatPromptTemplate（带异常防御）
        try:
            prompt_content = load_prompt("direct_answer")
        except Exception as pe:
            logger.warning(f"⚠️ [DirectAnswer] 加载外置 prompt 失败，启用系统兜底提示词: {pe}")
            prompt_content = (
                "你是一个专业、亲切的企业级智能问数助手。\n"
                "用户发起了日常闲聊或通用提问，请友好礼貌地回复，并引导用户提出具体的业务数据查询需求。\n"
                "用户问题：{query}"
            )

        chat_template = ChatPromptTemplate.from_template(prompt_content)
        # 组装完整的 PromptValue 消息体
        formatted_messages = chat_template.format_messages(query=query)

        full_answer = []

        # 4. 使用 astream 执行流式生成，逐 Token 推送给前端
        async for chunk in llm.astream(formatted_messages):
            delta = chunk.content if hasattr(chunk, "content") else str(chunk)
            if delta:
                full_answer.append(delta)
                if writer:
                    # 统一流式事件协议契约：推送到前端
                    writer({"type": "token", "content": delta})

        final_text = "".join(full_answer)
        logger.info(f"直接回答完成: {final_text}")
        return {"final_answer": final_text}

    except Exception as e:
        logger.error(f"直接回答失败: {e}")
        error_msg = f"抱歉，系统暂时无法回复该问题: {str(e)}"
        if writer:
            writer({"type": "token", "content": error_msg})
        return {"final_answer": error_msg}
