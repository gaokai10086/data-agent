"""异动归因 Agent 的业务数据结构。

这里定义“解析出了什么”，不保存 LangGraph 的执行进度。
"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class FilterCondition(BaseModel):
    """用户在问题中指定的筛选条件；维度名和值尚未与元数据核对。"""

    dimension: str
    value: str
    operator: Literal["="] = "="


class ExtractedRequest(BaseModel):
    """LLM 从原话提取的字段；年月和筛选条件尚未完成业务校验。"""

    metric: str
    target_year: int
    target_month: int
    comparison_year: int | None = None
    comparison_month: int
    filters: list[FilterCondition] = Field(default_factory=list)


class AnalysisRequest(BaseModel):
    """程序校验后的比较请求，不代表数据库已证实用户所说的下降。

    metric 仍是用户的指标名称；是否对应合法指标由后续节点确认。
    """

    metric: str
    target_period: str
    comparison_period: str
    comparison_type: Literal["mom", "yoy", "custom"]
    filters: list[FilterCondition] = Field(default_factory=list)


class MetricDimension(BaseModel):
    """一个可用于分组或筛选的指标维度。

    name 是面向业务的名称，例如“门店”；column 是 Metadata 中确认过的
    物理字段，例如 ``dim_store.store_name``。异动 Agent 只消费这份契约，
    不直接读取 SQLAlchemy ORM 对象。
    """

    name: str
    column: str
    aliases: list[str] = Field(default_factory=list)


class MetricContext(BaseModel):
    """从共享 Metadata 解析出的指标上下文。

    这是 Metric Context 节点的输出，也是 Planner 后续制定 DataRequest 的
    输入。它描述“指标怎么算、能按什么分析”，不包含任何查询结果。
    """

    metric_key: str
    name: str
    description: str = ""
    formula: str = ""
    fact_table: str
    measure_column: str
    aggregation: str
    time_column: str | None = None
    filters: list[dict[str, Any]] = Field(default_factory=list)
    dimensions: list[MetricDimension] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)


class OverallQueryResult(BaseModel):
    """总体指标查询结果，只保存两个比较周期的原始数值。"""

    request_id: str
    metric_key: str
    target_period: str
    comparison_period: str
    current_value: Any
    comparison_value: Any
