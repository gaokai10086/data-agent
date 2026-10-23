"""共享指标拆解契约：第一版只支持 数量 ×（目标金额 / 数量）。

这是分析关系，不是 SQL 取数公式，也不是允许任意执行的表达式。
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MetricReference(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    type: Literal["metric"] = "metric"
    metric_key: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")


class RatioReference(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    type: Literal["ratio"] = "ratio"
    numerator_metric_key: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    denominator_metric_key: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")


class CountFactor(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    factor_key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    source: MetricReference


class AverageFactor(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    factor_key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    source: RatioReference


class MetricDecomposition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    type: Literal["product"] = "product"
    factors: tuple[CountFactor, AverageFactor]
    scope_policy: Literal["inherit"] = "inherit"
    zero_denominator_policy: Literal["unavailable"] = "unavailable"

    @model_validator(mode="after")
    def check_relation(self):
        count, average = self.factors
        if count.factor_key == average.factor_key:
            raise ValueError("拆解因素标识不能重复")
        if count.source.metric_key != average.source.denominator_metric_key:
            raise ValueError("平均金额的分母必须引用第一个数量因素")
        if average.source.numerator_metric_key == count.source.metric_key:
            raise ValueError("金额指标与数量指标不能相同")
        return self


def sales_amount_decomposition() -> MetricDecomposition:
    """返回独立配置实例；平均订单金额是本次分析因素，不新建假想的已发布指标。"""
    return MetricDecomposition(factors=(
        CountFactor(factor_key="order_count", name="订单数量",
                    source=MetricReference(metric_key="order_count")),
        AverageFactor(factor_key="avg_order_amount", name="平均支付订单金额",
                      source=RatioReference(numerator_metric_key="sales_amount",
                                            denominator_metric_key="order_count")),
    ))
