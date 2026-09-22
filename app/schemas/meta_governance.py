"""字段语义治理的 HTTP 请求与响应模型。"""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SemanticDraftView(BaseModel):
    """待审核字段语义草稿的展示模型。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    qualified_name: str
    status: str
    revision: int
    candidate_description: str
    candidate_alias: list[str]
    candidate_value_examples: list[str]
    candidate_role: str | None = None
    model_name: str | None = None
    prompt_version: str | None = None
    reviewer: str | None = None
    review_comment: str | None = None
    created_at: datetime | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class SemanticDraftApproveRequest(BaseModel):
    """审核通过请求，审核人必填，其余字段用于覆盖候选后发布。"""

    reviewer: str = Field(min_length=1, description="审核人姓名")
    review_comment: str | None = Field(default=None, description="审核意见")
    description: str | None = Field(default=None, description="覆盖候选说明")
    alias: list[str] | None = Field(default=None, description="覆盖候选别名")
    examples: list[str] | None = Field(default=None, description="覆盖真实样例")
    role: Literal["primary_key", "foreign_key", "measure", "dimension"] | None = Field(
        default=None, description="修正字段角色"
    )


class SemanticDraftRejectRequest(BaseModel):
    """审核驳回请求。"""

    reviewer: str = Field(min_length=1, description="审核人姓名")
    review_comment: str | None = Field(default=None, description="驳回原因")
