"""AnomalyPreparationAgent 的私有 State。

前置 Agent 只负责形成可验证的总体变化上下文；跨 Agent 输出通过
PreparationResult 写入父图 State。
"""

from typing import TypedDict

from app.agent.anomaly_agent.schemas import (
    AnalysisPlan,
    AnalysisRequest,
    BaseAnomalyResult,
    ChangeVerification,
    MetricContext,
    PreparationResult,
)


class PreparationState(TypedDict, total=False):
    raw_question: str
    analysis_request: AnalysisRequest
    metric_context: MetricContext
    analysis_plan: AnalysisPlan
    analysis_result: BaseAnomalyResult
    change_verification: ChangeVerification
    preparation_result: PreparationResult
