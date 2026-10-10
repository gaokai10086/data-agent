"""智谱 GLM Provider。"""
from app.config.app_config import app_config
from app.core.llm.base import ProviderConfig
from app.core.llm.providers.openai_compatible import OpenAICompatibleProvider


class ZhipuProvider(OpenAICompatibleProvider):
    """通过智谱 OpenAI-compatible API 创建 GLM 模型。"""

    def __init__(self) -> None:
        super().__init__(
            ProviderConfig(
                name="zhipu",
                api_key=app_config.zhipu_llm.api_key,
                base_url=app_config.zhipu_llm.base_url,
                models=app_config.zhipu_llm.models,
            )
        )
