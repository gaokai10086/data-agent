"""异动归因 LangGraph 的共享状态，只描述节点间传递哪些数据。"""

from typing import TypedDict

from app.agent.anomaly_agent.schemas import (
    AnalysisPlan,
    AnalysisRequest,
    AnalysisStrategy,
    BaseAnomalyResult,
    ChangeVerification,
    ChangeAnalysisReport,
    MetricContext,
)


class AnomalyState(TypedDict, total=False):
    """节点逐步填充的状态；total=False 允许节点只返回本步更新的字段。"""

    # 用户从 Orchestrator 分流后传入的完整原始问题。例如：“为什么北京2025年7月销售额比6月下降了？”
    raw_question: str

    # 代码校验和归一化后的请求，供后续 Metric Context、Planner 等节点使用。
    analysis_request: AnalysisRequest

    # 从共享 Metadata 读取并归一化后的指标定义，供 Planner 生成 DataRequest。
    metric_context: MetricContext

    # 当前版本只支持 overall_comparison；后续由路由节点扩展。
    analysis_plan: AnalysisPlan

    # 通用分析结果；具体结构由 analysis_type 对应的结果子类约束。
    analysis_result: BaseAnomalyResult

    # 变化事实核实与后续分析决策；不进行历史异常检测。
    change_verification: ChangeVerification

    # Skill 制定的分析步骤；执行节点统一继承 analysis_request 的查询范围。
    analysis_strategy: AnalysisStrategy

    # 单节点内部的动作历史、观察结果及停止原因；节点完成后统一写回。
    change_analysis_report: ChangeAnalysisReport
