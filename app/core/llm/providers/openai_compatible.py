"""基于 OpenAI-compatible API 的通用 Chat Model 创建器。"""
from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

from app.core.llm.base import LLMProvider, ProviderConfig


class OpenAICompatibleProvider(LLMProvider):
    """OpenAI-compatible Provider 的公共实现。"""

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def models(self) -> dict[str, str]:
        return self.config.models

    def create(
        self,
        *,
        model_tier: str,
        temperature: float,
        streaming: bool,
        max_retries: int,
    ) -> BaseChatModel:
        model = self.models.get(model_tier)
        if model is None:
            supported = ", ".join(sorted(self.models))
            raise ValueError(f"{self.name} 不支持模型规格 {model_tier}，当前支持: {supported}")

        return ChatOpenAI(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            model=model,
            temperature=temperature,
            streaming=streaming,
            max_retries=max_retries,
        )
