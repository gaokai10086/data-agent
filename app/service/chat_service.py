from langchain_huggingface import HuggingFaceEndpointEmbeddings
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.data_agent.context import DataAgentContext
from app.config.app_config import DEFAULT_LLM_MODEL, DEFAULT_LLM_PROVIDER
from app.core.logging import logger
from app.repositories.es.value_es_repository import ValueESRepository
from app.repositories.mysql.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository
from app.repositories.qdrant.column_repository_qdrant import ColumnQdrantRepository
from app.repositories.qdrant.metric_repository_qdrant import MetricQdrantRepository

"""
核心职责：ChatService 是整个系统的“业务上下文打包器与状态机点火器”。它本身不包含具体业务算法
核心作用是将底层的静态基础设施（数据库、向量库、ES、模型）与动态用户输入打包，启动 LangGraph 状态图
并将图产出的事件块以异步流（AsyncGenerator）的形式实时向外传递
前置链路（：chat_router.py（FastAPI 的 SSE 接口层）通过 Depends(get_chat_service) 拿到该实例，调用 stream_chat 并将拿到的 chunk 包装为 data: ...\n\n 冲刷给前端浏览器.
后置链路（它在驱动谁）：进入 self.graph.astream(...)，即 LangGraph 状态图内部，驱动各个节点（Schema 召回 $\to$ 语义层指标匹配 $\to$ SQL 生成 $\to$ 查库与报错自愈 $\to$ 图表生成）按顺序或条件分支执行
"""

class ChatService:
    #  基础设施依赖的收敛与托管（__init__）：
    # 接收上一层装配好的 7 大武器（图状态机、Embedding 模型、两个 MySQL 仓储、两个 Qdrant 仓储、一个 ES 仓储）
    # 把它们聚合在当前服务实例内，避免散落在各处。
    def __init__(self, graph: CompiledStateGraph,
                 embedding_client: HuggingFaceEndpointEmbeddings,
                 meta_mysql_repository: MetaMySQLRepository,
                 dw_mysql_repository: DWMySQLRepository,
                 column_qdrant_repository: ColumnQdrantRepository,
                 value_es_repository: ValueESRepository,
                 metric_qdrant_repository: MetricQdrantRepository,
                 ):
        # 由 LangGraph 编译出的工作流状态图，所有的“找表、写 SQL、自愈报错、生成图表”都是这个图里的节点
        self.graph = graph
        self.embedding_client = embedding_client
        self.meta_mysql_repository = meta_mysql_repository
        self.dw_mysql_repository = dw_mysql_repository
        self.column_qdrant_repository = column_qdrant_repository
        self.value_es_repository = value_es_repository
        self.metric_qdrant_repository = metric_qdrant_repository


    def _build_context(self, *, provider: str = DEFAULT_LLM_PROVIDER, model_tier: str = DEFAULT_LLM_MODEL) -> DataAgentContext:
        """静态资源打包：把数据库、ES、Qdrant、Embedding 句柄组装成图运行上下文。"""
        return DataAgentContext(
            metric_qdrant_repository=self.metric_qdrant_repository,
            value_es_repository=self.value_es_repository,
            column_qdrant_repository=self.column_qdrant_repository,
            embedding_client=self.embedding_client,
            meta_mysql_repository=self.meta_mysql_repository,
            dw_mysql_repository=self.dw_mysql_repository,
            llm_provider=provider,
            llm_model_tier=model_tier,
        )

    async def stream_chat(self, query: str, *, provider: str = DEFAULT_LLM_PROVIDER, model_tier: str = DEFAULT_LLM_MODEL):
        logger.info("[Chat Service] selected provider={} model={}", provider, model_tier)
        # Orchestrator 负责意图识别和分流，子 Agent 在适配节点内接收自己的 State。
        context = self._build_context(provider=provider, model_tier=model_tier)
        #。
        async for chunk in self.graph.astream(
            input={"query": query}, context=context, stream_mode="custom"
        ):
            yield chunk

    async def run_once(self, query: str, *, provider: str = DEFAULT_LLM_PROVIDER, model_tier: str = DEFAULT_LLM_MODEL) -> dict:
        """非流式执行一次问数，返回最终 State，供离线评测与批量任务使用。"""
        context = self._build_context(provider=provider, model_tier=model_tier)
        result = await self.graph.ainvoke(input={"query": query}, context=context)
        return result.get("agent_result") or result

#   为什么要把数据拆分为 DataAgentState 和 DataAgentContext 两个不同的对象
"""
State 专管业务状态：代表流转中的数据,如提问、生成的 SQL、错误日志、重试次数。它是轻量级的纯数据模型，便于状态快照（Checkpointer）保存与版本回滚；
Context 专管系统资源：包含数据库连接池、ES 客户端、向量库实例。重型句柄无法且不应该被序列化存储。
"""

#  LangGraph 支持多种流式模式（如 values、updates、messages），为什么在复杂的 BI 问数链路中推荐使用 stream_mode="custom"？
"""
作用：stream_mode是LangGraph 中 astream() / stream() 方法的核心控制参数，用来决定状态图在运行时吐出什么样粒度、什么样类型的数据
主要模式：LangGraph 原生支持多种流式模式（支持单选或组合传入）：
values：图每走一步，吐出当前完整的全局状态快照；
updates：图每走一步，仅吐出当前节点对状态所做的增量修改；
messages：专门针对大模型输出，流式吐出大模型生成的 Token 块与消息元数据；
custom：由开发者完全自主控制，允许在节点内部手动推送任意自定义业务事件（如进度通知、SQL 语句、图表 JSON）；向前端精准通报“正在召回”、“SQL 已生成”、“图表已绘制”等业务事件
debug：吐出全链路底层调试信息（节点执行细节、状态转移等）。
"""
