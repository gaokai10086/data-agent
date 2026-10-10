"""异动归因 LangGraph 的共享状态，只描述节点间传递哪些数据。"""

from typing import TypedDict

from app.agent.anomaly_agent.schemas import AnalysisRequest, MetricContext, OverallQueryResult


class AnomalyState(TypedDict, total=False):
    """节点逐步填充的状态；total=False 允许节点只返回本步更新的字段。"""

    # 用户从 Orchestrator 分流后传入的完整原始问题。例如：“为什么北京2025年7月销售额比6月下降了？”
    raw_question: str

    # 代码校验和归一化后的请求，供后续 Metric Context、Planner 等节点使用。
    analysis_request: AnalysisRequest

    # 从共享 Metadata 读取并归一化后的指标定义，供 Planner 生成 DataRequest。
    metric_context: MetricContext

    # 总体查询得到的两个周期原始值，供变化率和后续归因节点使用。
    overall_result: OverallQueryResult
