"""DimensionAttributionAgent 的独立 LangGraph State。"""

from typing import TypedDict

from app.agent.anomaly_agent.DimensionAttribution_Agent.schemas import (
    AttributionDecision,
    AttributionObservation,
    AttributionResult,
    AttributionTask,
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
    # 当前主路径的有效范围；由程序从已对账路径推导，不接受 LLM 直接覆盖。
    current_scope: list[object]
    selected_member: object
    selected_path_task_id: str | None
    abandoned_paths: list[dict]
    pending_tasks: list[AttributionTask]
