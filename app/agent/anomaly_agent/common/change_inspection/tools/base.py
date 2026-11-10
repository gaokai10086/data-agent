"""异动分析处理器的统一接口。"""

from abc import ABC, abstractmethod

from langgraph.runtime import Runtime

from app.agent.anomaly_agent.common.change_inspection.state import PreparationState
from app.agent.anomaly_agent.schemas import (
    AnalysisRequest,
    BaseAnomalyResult,
    MetricContext,
)
from app.agent.data_agent.context import DataAgentContext


class BaseAnomalyHandler(ABC):
    """一个分析类型的业务执行器。"""

    analysis_type: str

    @abstractmethod
    async def compare(
        self,
        request: AnalysisRequest,
        metric_context: MetricContext,
        runtime: Runtime[DataAgentContext],
    ) -> BaseAnomalyResult:
        """不依赖 Graph State 的领域执行入口。"""

    @abstractmethod
    async def run(
        self,
        state: PreparationState,
        runtime: Runtime[DataAgentContext],
    ) -> BaseAnomalyResult:
        """兼容现有 Graph 节点的 State 适配入口。"""

