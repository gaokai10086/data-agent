"""DeepSeek Provider。"""
from app.config.app_config import app_config
from app.core.llm.base import ProviderConfig
from app.core.llm.providers.openai_compatible import OpenAICompatibleProvider


class DeepSeekProvider(OpenAICompatibleProvider):
    def __init__(self) -> None:
        super().__init__(
            ProviderConfig(
                name="deepseek",
                api_key=app_config.llm.api_key,
                base_url=app_config.llm.base_url,
                models={model: model for model in app_config.llm.models},
            )
        )
