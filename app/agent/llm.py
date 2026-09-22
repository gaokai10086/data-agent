import asyncio
import logging
from functools import lru_cache
from typing import Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

from app.config.app_config import app_config

# 配置日志
logger = logging.getLogger(__name__)

# ============ 常量配置 ============
DEEPSEEK_API_KEY = app_config.llm.api_key
LLM_BASE_URL = getattr(app_config.llm, 'base_url', 'https://api.deepseek.com')

# 模型矩阵：定义不同提供商的模型规格
MODEL_MATRIX = {
    "deepseek": {
        "flash": "deepseek-chat",  # 快速模型
        "pro": "deepseek-reasoner",  # 深度推理模型
    },
}


@lru_cache(maxsize=16)
def get_llm(
        provider: Literal["deepseek"] = "deepseek",
        model_tier: Literal["flash", "pro"] = "flash",
        temperature: float = 0.3,
        streaming: bool = False,
        max_retries: int = 3,
) -> BaseChatModel:
    """
    大模型工厂接口（支持单例缓存）

    Args:
        provider: 模型提供商，目前仅支持 deepseek
        model_tier: 模型规格，flash（快速）或 pro（深度推理）
        temperature: 采样温度，范围 [0.0, 2.0]
        streaming: 是否启用流式输出
        max_retries: 最大重试次数

    Returns:
        BaseChatModel: LangChain 模型实例
    """
    # 1. 温度安全检查
    if not (0.0 <= temperature <= 2.0):
        logger.warning(f"⚠️ 温度 {temperature} 超出范围，已自动修正为 0.3")
        temperature = 0.3

    # 2. 提供商校验
    selected_provider = provider.lower()
    if selected_provider not in MODEL_MATRIX:
        raise ValueError(f"❌ 不支持的提供商: '{selected_provider}'，仅支持 'deepseek'")

    # 3. 模型规格校验
    if model_tier not in MODEL_MATRIX[selected_provider]:
        raise ValueError(f"❌ '{selected_provider}' 不支持 '{model_tier}' 规格")

    target_model = MODEL_MATRIX[selected_provider][model_tier]

    # 4. 创建 DeepSeek 实例
    logger.info(
        f"🐋 [LLM Factory] 加载 DeepSeek | 规格: {model_tier.upper()} ({target_model}) | "
        f"Temp: {temperature} | 流式: {streaming}"
    )

    return ChatOpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url=LLM_BASE_URL,
        model=target_model,
        temperature=temperature,
        streaming=streaming,
        max_retries=max_retries,
    )


# ============ 模块级默认实例（供其他模块导入）============
llm = get_llm(
    provider="deepseek",
    model_tier="flash",
    temperature=0.3,
    streaming=False
)

# ============ 测试代码 ============
if __name__ == '__main__':
    async def test():
        """测试大模型连接"""
        try:
            response = await llm.ainvoke("中国的首都是哪里？")
            print(f"✅ 大模型响应: {response.content}")
        except Exception as e:
            print(f"❌ 测试失败: {e}")


    asyncio.run(test())
