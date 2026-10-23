from pydantic import BaseModel, Field
from uuid import uuid4

from app.config.app_config import DEFAULT_LLM_MODEL, DEFAULT_LLM_PROVIDER



class QuerySchema(BaseModel):
    query: str
    session_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=128)
    request_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=128)
    provider: str = DEFAULT_LLM_PROVIDER
    model_tier: str = DEFAULT_LLM_MODEL
