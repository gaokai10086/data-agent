"""异动分析类型到处理器的动态路由表。"""

from app.agent.anomaly_agent.Change_Inspection_Agent.tools.base import BaseAnomalyHandler
from app.agent.anomaly_agent.Change_Inspection_Agent.tools.overall_comparison import OverallComparisonHandler


class UnsupportedAnalysisTypeError(ValueError):
    """当前版本没有对应异动处理器。"""


HANDLER_REGISTRY: dict[str, BaseAnomalyHandler] = {
    "overall_comparison": OverallComparisonHandler(),
}


def get_anomaly_handler(analysis_type: str) -> BaseAnomalyHandler:
    """根据分析类型获取处理器。"""

    handler = HANDLER_REGISTRY.get(analysis_type)
    if handler is None:
        raise UnsupportedAnalysisTypeError(
            f"暂不支持异动分析类型：{analysis_type}"
        )
    return handler
