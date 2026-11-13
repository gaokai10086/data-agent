"""Main_RootCause_Agent 的任务 Schema 与注册表。

Schema 只描述用户分析请求的格式，不代表对应专业 Agent 已实现。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agent.anomaly_agent.schemas import FilterCondition, MonthPeriod


class PeriodComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    metric: str | None = None
    target_period: MonthPeriod | None = None
    comparison_period: MonthPeriod | None = None
    filters: list[FilterCondition] = Field(default_factory=list)
    expected_direction: Literal["decrease", "increase", "unspecified"] = "unspecified"
    missing_info: list[str] = Field(default_factory=list)


class AnomalyLocationRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    metrics: list[str] = Field(default_factory=list)
    relative_time: str | None = None
    target_period: MonthPeriod | None = None
    comparison_period: MonthPeriod | None = None
    period: MonthPeriod | None = None
    dimensions: list[str] = Field(default_factory=list)
    sort_order: Literal["descending", "ascending"] | None = None
    ranking: int | None = None
    missing_info: list[str] = Field(default_factory=list)


class DimensionAttributionRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    metric: str | None = None
    target_period: MonthPeriod | None = None
    comparison_period: MonthPeriod | None = None
    filters: list[FilterCondition] = Field(default_factory=list)
    expected_direction: Literal["decrease", "increase", "unspecified"] = "unspecified"
    dimensions: list[str] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)


class MetricDecompositionRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    metric: str | None = None
    decomposition_metrics: list[str] = Field(default_factory=list)
    relationship: str | None = None
    target_period: MonthPeriod | None = None
    comparison_period: MonthPeriod | None = None
    filters: list[FilterCondition] = Field(default_factory=list)
    decomposition_type: str | None = None
    missing_info: list[str] = Field(default_factory=list)


class HypothesisAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    metric: str | None = None
    target_period: MonthPeriod | None = None
    comparison_period: MonthPeriod | None = None
    hypothesis: str | None = None
    factors: list[str] = Field(default_factory=list)
    evidence_requirements: list[str] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)


TASK_SCHEMA_REGISTRY: dict[str, type[BaseModel]] = {
    "period_comparison": PeriodComparisonRequest,
    "anomaly_location": AnomalyLocationRequest,
    "dimension_attribution": DimensionAttributionRequest,
    "metric_decomposition": MetricDecompositionRequest,
    "hypothesis_analysis": HypothesisAnalysisRequest,
}


def get_task_schema(task_type: str) -> type[BaseModel]:
    """按注册任务类型返回 Schema 模型；不支持任意动态导入。"""
    schema = TASK_SCHEMA_REGISTRY.get(task_type)
    if schema is None:
        raise ValueError(f"未注册任务 Schema：{task_type}")
    return schema
