from fastapi import Depends

from app.agent.orchestrator.graph import orchestrator_graph as graph
from app.clients.embedding_client import embedding_client_manager
from app.clients.mysql_client import dw_client_manager, meta_client_manager
from app.clients.qdrant_client import qdrant_client_manager
from app.repositories.es.value_es_repository import ValueESRepository
from app.repositories.mysql.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository
from app.repositories.qdrant.column_repository_qdrant import ColumnQdrantRepository
from app.repositories.qdrant.metric_repository_qdrant import MetricQdrantRepository
from app.service.chat_service import ChatService
from app.clients.es_client import es_client_manager

"""
将用到的工具实例注入的注册中心
1. 安全发放数据库钥匙：按需创建并自动回收数仓与元数据的 MySQL 数据库会话（Session），防止连接泄露。
2. 组装各个专业工具（Repository / Client）：把 MySQL、Qdrant 向量库、Elasticsearch 倒排索引、Embedding 模型和 LangGraph 图状态机逐一包装成可以随时干活的专业仓库对象。
3. 打包交付给指挥官（ChatService）：将上述组装好的 7 大核心组件统一塞给 ChatService，供路由接口随时调用。
"""

"""数据库会话生命周期管理
系统区分了 DW 库（数仓业务数据） 和 Meta 库（表结构元数据）
接口开始处理时借出一个连接会话，问答完毕或报错后强制自动关闭并归还给连接池，避免数据库连接数被撑爆
"""
# 获取 DW 库 Session,数仓库，真正执行生成的 SQL 并查出销售额、播放量的地方
async def get_dw_session():
    async with dw_client_manager.session_factory() as session:
        yield session
# 获取 Meta 库 Session,元数据库，记录了“系统有哪些表、哪些字段代表什么意思”
async def get_meta_session():
    async with meta_client_manager.session_factory() as session:
        yield session


"""
1. 结构查询：MetaMySQLRepository 查表结构定义，DWMySQLRepository 执行生成好的最终 SQL。
2. 语义召回（Qdrant）：ColumnQdrantRepository 检索语义相似的字段名，MetricQdrantRepository 检索标准化指标公式（如完播率、ROI）。
3. 精准匹配（ES）：ValueESRepository 专门用来对具体的枚举值（如品牌名“完美日记”、达人名“老罗”）做倒排索引匹配，防止向量检索跑偏。
4. 算法底座：提供文本向量化模型（Embedding Client）与执行状态图（Graph）。
"""
# MySQL 仓库: 获取 Meta Repository
async def get_meta_repository(meta_session=Depends(get_meta_session)):
    #   专门找表结构元数据
    return MetaMySQLRepository(meta_session)
# 获取 DW Repository
async def get_dw_repository(dw_session=Depends(get_dw_session)):
    #  专门用来执行只读 SQL 拿业务数据。
    return DWMySQLRepository(dw_session)

# Qdrant 向量仓库（语义理解）：获取 字段 Qdrant Repository
async def get_column_qdrant_repository():
    #  用户问“带货表现”，Qdrant 用向量比对出 gmv、sales_volume 字段
    return ColumnQdrantRepository(qdrant_client_manager.client)
# 获取 指标 Qdrant Repository
async def get_metric_qdrant_repository():
    #  对应我们之前讲的语义指标层（MDL），检索“完播率”、“ROI”的标准计算公式。
    return MetricQdrantRepository(qdrant_client_manager.client)
# ES 仓库（精确与模糊匹配）:获取 字段值 ES Repository
async def get_value_es_repository():
    #  向量数据库检索数字或具体名字往往不准。用 ES 做倒排索引查字段值枚举，精准率极高
    return ValueESRepository(es_client_manager.client)
# 图状态机与向量模型 获取 Embedding Client
async def get_embedding_client():
    #  负责把用户的自然语言转成向量。
    return embedding_client_manager.client


# 图状态机与向量模型: 获取 Graph
async def get_graph():
    #  直接注入 LangGraph 编译好的执行图
    return graph

# FastAPI 的 Depends 会像拼积木一样，自动先调用上面所有的小零件
# 最后把这 7 个强力组件作为构造参数，实例化出一个全副武装的 ChatService 返回给上一节的路由。
async def get_chat_service(
        graph=Depends(get_graph),
        meta_repository=Depends(get_meta_repository),
        dw_repository=Depends(get_dw_repository),
        column_qdrant_repository=Depends(get_column_qdrant_repository),
        metric_qdrant_repository=Depends(get_metric_qdrant_repository),
        value_es_repository=Depends(get_value_es_repository),
    embedding_client=Depends(get_embedding_client),
) -> ChatService:
    return ChatService(
        graph=graph,
        meta_mysql_repository=meta_repository,
        dw_mysql_repository=dw_repository,
        column_qdrant_repository=column_qdrant_repository,
        metric_qdrant_repository=metric_qdrant_repository,
        value_es_repository=value_es_repository,
        embedding_client=embedding_client,
    )

#  为什么要在 deps.py 里搞这么复杂的依赖拆分，而不是直接在 ChatService 内部通过 import 去连接各种数据库？
"""
1. 保障了底层组件的可拔插性。比如业务服务 ChatService 依赖的是仓储抽象，如果未来底层数仓由 MySQL 迁移至 ClickHouse，
或者向量数据库从 Qdrant 切换为 Milvus，我只需要新增对应的 Repository 实现类并在 deps.py 中重定向组装，上层几十个问数场景业务代码完全零入侵、零修改，严格遵循了开闭原则。
2. 精确管控资源生命周期。通过 async with ... yield 确保了数据库会话与客户端在单次请求维度的安全释放，从机制上杜绝了连接泄露。
3. 提供了极佳的可测性（Testability）。在编写单测时，我们可以零成本覆盖依赖项，注入 Mock 仓储，不需要依赖真实的向量库和数据库环境即可完成核心业务流的集成验证。”
"""
