"""LLM Provider 的最小配置契约。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from langchain_core.language_models.chat_models import BaseChatModel


@dataclass(frozen=True)
class ProviderConfig:
    """一个模型供应商创建 OpenAI-compatible Chat Model 所需的配置。"""

    name: str
    api_key: str
    base_url: str
    models: dict[str, str]


class LLMProvider(ABC):
    """所有模型供应商必须实现的统一接口。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider 注册名称。"""

    @property
    @abstractmethod
    def models(self) -> dict[str, str]:
        """模型规格键到真实 API 模型 ID 的映射。"""

    @abstractmethod
    def create(
        self,
        *,
        model_tier: str,
        temperature: float,
        streaming: bool,
        max_retries: int,
    ) -> BaseChatModel:
        """创建 LangChain Chat Model。"""
