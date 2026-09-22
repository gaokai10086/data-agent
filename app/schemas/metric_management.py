"""指标管理 HTTP 请求与响应模型。"""
from typing import Any, Literal

from pydantic import BaseModel, Field


class MetricFilterItem(BaseModel):
    column: str = Field(description="过滤字段，支持 表名.字段名 或 fact_table 内的字段名")
    operator: str = Field(description="比较运算符，例如 =、!=、IN、BETWEEN")
    value: Any = Field(description="过滤值，由业务人员填写")


class MetricDimensionItem(BaseModel):
    column: str = Field(description="分析维度字段，支持 表名.字段名")
    name: str | None = Field(default=None, description="维度展示名称，可选")


class MetricSubmitRequest(BaseModel):
    """业务人员提交的新指标。提交即发布，但仍会经过系统技术校验。"""

    metric_key: str = Field(min_length=1, description="稳定英文标识，例如 sales_amount")
    name: str = Field(min_length=1, description="中文指标名称，例如 销售额")
    description: str = Field(min_length=1, description="业务口径说明")
    formula: str = Field(min_length=1, description="计算公式")
    fact_table: str = Field(min_length=1, description="主事实表")
    measure_column: str = Field(min_length=1, description="核心度量字段")
    aggregation: Literal["SUM", "COUNT", "COUNT_DISTINCT", "AVG", "MIN", "MAX"]
    time_column: str | None = Field(default=None, description="默认时间字段")
    filters: list[MetricFilterItem] = Field(default_factory=list, description="固定过滤条件")
    alias: list[str] = Field(default_factory=list, description="业务别名")
    dimensions: list[MetricDimensionItem] = Field(default_factory=list, description="可分析维度")
