#  导入异步上下文管理器
from contextlib import asynccontextmanager
# 导入FastAPI
from fastapi import FastAPI

#  导入各种客户端管理器
from app.clients.embedding_client import embedding_client_manager #  嵌入模型客户端
from app.clients.es_client import es_client_manager #  Elasticsearch客户端
from app.clients.mysql_client import dw_client_manager, meta_client_manager #   MySQL客户端
from app.clients.qdrant_client import qdrant_client_manager #  Qdrant向量库客户端

"""
生命周期管理是什么？
❌ 没有生命周期管理时的困境：
资源散乱管理
├── 问题1：数据库连接在路由中创建，无法复用
├── 问题2：应用关闭时连接未释放，导致泄漏
└── 问题3：初始化代码分散，难以维护
✅ 有了生命周期管理后的优势：
统一资源管理
├── 优势1：应用启动时统一初始化
├── 优势2：应用关闭时统一释放
└── 优势3：集中管理，易于维护
"""

#  定义异步上下文管理器
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 第13-18行：启动阶段 ⭐
    dw_client_manager.init()  # 初始化数据仓库连接
    meta_client_manager.init()  # 初始化元数据库连接
    es_client_manager.init()  # 初始化ES连接
    qdrant_client_manager.init()  # 初始化向量库连接
    embedding_client_manager.init()  # 初始化嵌入模型

    # 第19行：yield 分隔符 ⭐
    yield  # 应用运行中，等待关闭信号

    # 第20-24行：关闭阶段 ⭐
    await dw_client_manager.close()  # 关闭数据仓库连接
    await meta_client_manager.close()  # 关闭元数据库连接
    await es_client_manager.close()  # 关闭ES连接
    await qdrant_client_manager.close()  # 关闭向量库连接
