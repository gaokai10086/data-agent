"""异动分析顶层 Graph 的状态契约。"""

from typing import Literal, TypedDict

from app.agent.anomaly_agent.schemas import (
    AnalysisPlan,
    AnalysisRequest,
    AnalysisStrategy,
    BaseAnomalyResult,
    ChangeAnalysisReport,
    ChangeVerification,
    MetricContext,
    PreparationResult,
)


class AnomalyState(TypedDict, total=False):
    """主 Agent 与变化核查子 Agent 之间共享的最小状态。"""

    raw_question: str
    intent: Literal["anomaly_analysis"]
    selected_skill_name: str
    analysis_request: AnalysisRequest
    metric_context: MetricContext
    analysis_plan: AnalysisPlan
    analysis_result: BaseAnomalyResult
    change_verification: ChangeVerification
    preparation_result: PreparationResult
    analysis_strategy: AnalysisStrategy
    change_analysis_report: ChangeAnalysisReport
