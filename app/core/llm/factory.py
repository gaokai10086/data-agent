"""LLM 工厂：Agent 只通过这里获取模型实例。"""
from __future__ import annotations

import asyncio
import logging
from functools import lru_cache

from langchain_core.language_models.chat_models import BaseChatModel

from app.config.app_config import DEFAULT_LLM_MODEL, DEFAULT_LLM_PROVIDER
from app.core.llm.base import LLMProvider
from app.core.llm.providers.deepseek import DeepSeekProvider
from app.core.llm.providers.zhipu import ZhipuProvider

logger = logging.getLogger(__name__)


class LLMFactory:
    """统一创建不同 Provider、规格和调用模式的 LLM 实例。"""

    _providers: dict[str, LLMProvider] = {}

    @classmethod
    def register(cls, provider: LLMProvider) -> None:
        """注册一个 Provider 实例；同名注册会覆盖旧实例。"""
        cls._providers[provider.name.lower()] = provider

    @classmethod
    def available_providers(cls) -> list[str]:
        """返回已经注册、可以被前端选择的 Provider。"""
        return sorted(cls._providers)

    @classmethod
    def available_model_tiers(cls, provider: str = DEFAULT_LLM_PROVIDER) -> list[str]:
        """返回指定 Provider 已注册的模型规格。"""
        provider_instance = cls._providers.get(provider.lower())
        if provider_instance is None:
            return []
        return sorted(provider_instance.models)

    @classmethod
    def model_name(cls, provider: str, model_tier: str) -> str:
        """返回前端展示用的真实模型版本名。"""
        provider_instance = cls._providers.get(provider.lower())
        if provider_instance is None:
            return model_tier
        return provider_instance.models.get(model_tier, model_tier)

    @classmethod
    def create(
        cls,
        *,
        provider: str = DEFAULT_LLM_PROVIDER,
        model_tier: str = DEFAULT_LLM_MODEL,
        temperature: float = 0.3,
        streaming: bool = False,
        max_retries: int = 3,
    ) -> BaseChatModel:
        if not 0.0 <= temperature <= 2.0:
            logger.warning("温度 %.2f 超出范围，已自动修正为 0.3", temperature)
            temperature = 0.3

        selected_provider = provider.lower()
        provider_instance = cls._providers.get(selected_provider)
        if provider_instance is None:
            supported = ", ".join(sorted(cls._providers))
            raise ValueError(f"不支持的模型提供商: {selected_provider}，当前支持: {supported}")

        target_model = provider_instance.models.get(model_tier)
        if target_model is None:
            supported_tiers = ", ".join(sorted(provider_instance.models))
            raise ValueError(
                f"{selected_provider} 不支持模型规格 {model_tier}，当前支持: {supported_tiers}"
            )

        logger.info(
            "[LLM Factory] provider=%s model=%s tier=%s temperature=%s streaming=%s",
            selected_provider,
            target_model,
            model_tier,
            temperature,
            streaming,
        )
        return provider_instance.create(
            model_tier=model_tier,
            temperature=temperature,
            streaming=streaming,
            max_retries=max_retries,
        )


LLMFactory.register(DeepSeekProvider())
LLMFactory.register(ZhipuProvider())


@lru_cache(maxsize=32)
def get_llm(
    provider: str = DEFAULT_LLM_PROVIDER,
    model_tier: str = DEFAULT_LLM_MODEL,
    temperature: float = 0.3,
    streaming: bool = False,
    max_retries: int = 3,
) -> BaseChatModel:
    """兼容现有调用方的工厂函数，实际创建委托给 ``LLMFactory``。"""
    return LLMFactory.create(
        provider=provider,
        model_tier=model_tier,
        temperature=temperature,
        streaming=streaming,
        max_retries=max_retries,
    )


def get_context_llm(
    runtime,
    *,
    temperature: float = 0.3,
    streaming: bool = False,
    max_retries: int = 3,
    default_provider: str = DEFAULT_LLM_PROVIDER,
    default_model_tier: str = DEFAULT_LLM_MODEL,
) -> BaseChatModel:
    """根据 LangGraph Runtime Context 创建当前请求使用的模型。

    Web 请求选择的 Provider 和模型规格会放在 Context 中；节点只需要调用
    此函数，不需要感知前端请求或环境变量。
    """
    context = getattr(runtime, "context", None) or {}
    provider = context.get("llm_provider", default_provider)
    model_tier = context.get("llm_model_tier", default_model_tier)
    return get_llm(
        provider=provider,
        model_tier=model_tier,
        temperature=temperature,
        streaming=streaming,
        max_retries=max_retries,
    )


if __name__ == "__main__":
    async def test() -> None:
        response = await get_llm(
            provider="deepseek",
            model_tier="deepseek-v4-flash",
        ).ainvoke("中国的首都是哪里？")
        print(response.content)

    asyncio.run(test())
