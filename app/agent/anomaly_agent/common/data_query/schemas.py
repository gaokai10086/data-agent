"""公共数据查询工具的输入输出契约。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DataQueryRequest(BaseModel):
    """一次面向数据仓库的结构化查询请求。

    ``query`` 仍然交给现有 Data Agent 生成和校验 SQL；调用方只需要说明
    业务问题和结果约束，不直接传入可执行 SQL。
    """

    query: str = Field(min_length=1)
    request_id: str
    purpose: str = "agent_query"
    role: str = ""
    period: str = ""
    metric_key: str = ""
    expected_columns: list[str] = Field(default_factory=list)
    max_rows: int | None = Field(default=None, gt=0)


class DataQueryResult(BaseModel):
    """公共查询工具返回的稳定结果，不暴露 DataAgentState。"""

    success: bool
    rows: list[dict[str, Any]] = Field(default_factory=list)
    sql: str | None = None
    columns: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    request_id: str
    purpose: str
    metadata: dict[str, Any] = Field(default_factory=dict)

