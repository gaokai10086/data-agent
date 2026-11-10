"""DimensionAttributionAgent 的独立 LangGraph State。"""

from typing import TypedDict

from app.agent.anomaly_agent.DimensionAttribution_Agent.schemas import (
    AttributionDecision,
    AttributionObservation,
    AttributionResult,
    DimensionAttributionInput,
)


class DimensionAttributionState(TypedDict, total=False):
    input: DimensionAttributionInput
    raw_question: str
    request_id: str
    metric_context: object
    inspection_outcome: object
    skill_strategy: object
    available_tool_names: tuple[str, ...]
    decisions: list[AttributionDecision]
    observations: list[AttributionObservation]
    analyzed_dimensions: list[str]
    round_count: int
    query_count: int
    current_depth: int
    stop_reason: str
    decision: AttributionDecision
    result: AttributionResult
    attributions: list[object]
    reconciliation: object
    sql_by_period: dict[str, str]
    paths: list[object]
    analyzed_task_ids: list[str]
