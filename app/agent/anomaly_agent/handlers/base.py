"""异动分析处理器的统一接口。"""

from abc import ABC, abstractmethod

from langgraph.runtime import Runtime

from app.agent.anomaly_agent.schemas import BaseAnomalyResult
from app.agent.anomaly_agent.state import AnomalyState
from app.agent.data_agent.context import DataAgentContext


class BaseAnomalyHandler(ABC):
    """一个分析类型的业务执行器。"""

    analysis_type: str

    @abstractmethod
    async def run(
        self,
        state: AnomalyState,
        runtime: Runtime[DataAgentContext],
    ) -> BaseAnomalyResult:
        """执行分析并返回已校验的领域结果。"""

