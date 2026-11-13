"""MetricDecompositionAgent 的独立 LangGraph State。"""

from typing import TypedDict

from app.agent.anomaly_agent.MetricDecomposition_Agent.schemas import (
    DecompositionObservation,
    DecompositionResult,
    MetricDecompositionInput,
)


class MetricDecompositionState(TypedDict, total=False):
    input: MetricDecompositionInput
    request_id: str
    formula: object
    observations: list[DecompositionObservation]
    components: list[object]
    reconciliation: object
    stop_reason: str
    result: DecompositionResult

