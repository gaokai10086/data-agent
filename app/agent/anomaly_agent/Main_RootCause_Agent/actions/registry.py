"""最小 Action 注册表。

Action 名称是受控枚举，不能由 LLM 自由拼写；执行能力必须先注册。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from app.agent.anomaly_agent.common.change_inspection.tool import verify_metric_change
from app.agent.anomaly_agent.schemas import AnalysisRequest, InspectionOutcome
from app.agent.anomaly_agent.DimensionAttribution_Agent.graph import dimension_attribution_graph
from app.agent.anomaly_agent.DimensionAttribution_Agent.schemas import DimensionAttributionInput
from app.agent.anomaly_agent.DimensionAttribution_Agent.schemas import AttributionResult
from app.agent.anomaly_agent.MetricDecomposition_Agent.graph import metric_decomposition_graph
from app.agent.anomaly_agent.MetricDecomposition_Agent.schemas import (
    DecompositionResult,
    MetricDecompositionInput,
)
from app.agent.anomaly_agent.Main_RootCause_Agent.diagnosis_support.tools import ScopedMetricDecompositionTool


@dataclass(frozen=True)
class AttributionExecution:
    result: AttributionResult
    nodes: list[str]


@dataclass(frozen=True)
class DecompositionExecution:
    result: DecompositionResult
    nodes: list[str]


async def run_dimension_attribution(input_data: DimensionAttributionInput, runtime: Any):
    state: dict[str, Any] = {}
    nodes: list[str] = []
    stream = dimension_attribution_graph.astream(
        {"input": input_data}, context=runtime.context, stream_mode=["custom", "updates", "values"],
    )
    try:
        async for mode, chunk in stream:
            if mode == "custom" and hasattr(runtime, "stream_writer"):
                runtime.stream_writer(chunk)
            if mode == "updates" and isinstance(chunk, dict):
                nodes.extend(str(name) for name in chunk)
            if mode == "values" and isinstance(chunk, dict):
                state.update(chunk)
    finally:
        await stream.aclose()
    return AttributionExecution(result=state["result"], nodes=nodes)


async def run_metric_decomposition(input_data: MetricDecompositionInput, runtime: Any):
    """执行独立指标拆解 Graph，并只返回其结构化结果和节点证据。"""
    state: dict[str, Any] = {}
    nodes: list[str] = []
    stream = metric_decomposition_graph.astream(
        {"input": input_data}, context=runtime.context, stream_mode=["custom", "updates", "values"],
    )
    try:
        async for mode, chunk in stream:
            if mode == "custom" and hasattr(runtime, "stream_writer"):
                runtime.stream_writer(chunk)
            if mode == "updates" and isinstance(chunk, dict):
                nodes.extend(str(name) for name in chunk)
            if mode == "values" and isinstance(chunk, dict):
                state.update(chunk)
    finally:
        await stream.aclose()
    return DecompositionExecution(result=state["result"], nodes=nodes)


ActionExecutor = Callable[
    [AnalysisRequest, Any],
    Awaitable[InspectionOutcome],
]

# 变化检验、两个专业分析及最多若干个成员范围补查；SQL 仍受 Skill 总预算约束。
MAX_ACTION_EXECUTIONS = 10


@dataclass(frozen=True)
class ActionSpec:
    name: str
    capability: str
    task_type: str
    executor: Callable[..., Awaitable[Any]]


ACTION_REGISTRY: dict[str, ActionSpec] = {
    "change_inspection": ActionSpec(
        name="change_inspection",
        capability="change_inspection",
        task_type="period_comparison",
        executor=verify_metric_change,
    ),
    "dimension_attribution": ActionSpec(
        name="dimension_attribution",
        capability="dimension_attribution",
        task_type="dimension_attribution",
        executor=run_dimension_attribution,
    ),
    "metric_decomposition": ActionSpec(
        name="metric_decomposition",
        capability="metric_decomposition",
        task_type="metric_decomposition",
        executor=run_metric_decomposition,
    ),
    "scoped_metric_decomposition": ActionSpec(
        name="scoped_metric_decomposition",
        capability="business_diagnosis",
        task_type="hypothesis_analysis",
        executor=ScopedMetricDecompositionTool().run,
    ),
}


def get_action(name: str) -> ActionSpec:
    action = ACTION_REGISTRY.get(name)
    if action is None:
        raise ValueError(f"未注册 Action：{name}")
    return action
