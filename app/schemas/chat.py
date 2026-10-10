from pydantic import BaseModel

from app.config.app_config import DEFAULT_LLM_MODEL, DEFAULT_LLM_PROVIDER



class QuerySchema(BaseModel):
    query: str
    provider: str = DEFAULT_LLM_PROVIDER
    model_tier: str = DEFAULT_LLM_MODEL
