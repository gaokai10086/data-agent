"""
================================================================================
脚本名称：全自动元数据探查与知识库构建器 (One-Click Schema Sync) 🚀
脚本路径：app/scripts/build_meta_knowledge.py
核心功能：
    1. 自动连接数仓 (dw)，通过 MySQLConnector 探查所有物理表与列
    2. 自动完成向量化 (Qdrant) 与 枚举采样 (ES) 的全链路写入
    3. 无需再手工编写或依赖外部 YAML 配置文件
================================================================================
"""
import asyncio
from app.config.app_config import app_config
from app.clients.embedding_client import embedding_client_manager
from app.clients.mysql_client import meta_client_manager
from app.clients.qdrant_client import qdrant_client_manager
from app.clients.es_client import es_client_manager
from app.core.datasource.mysql_connector import MySQLConnector
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository
from app.service.meta_knowledge_service import MetaKnowledgeService


# 可选：如果有一些非物理列能够直接表达的复杂分析指标，可以在这里轻量定义
DEFAULT_CORE_METRICS = [
    {
        "name": "各品类销售额占比",
        "description": "某个品类的销售总金额占全部品类销售金额的比例",
        "formula": "SUM(sales_amount) / SUM(SUM(sales_amount)) OVER()",
        "alias": ["品类贡献率", "品类占比"]
    },
    {
        "name": "客单价",
        "description": "平均每个买家的实付消费金额",
        "formula": "SUM(sales_amount) / COUNT(DISTINCT user_id)",
        "alias": ["ATV", "平均购买金额"]
    }
]


async def run_auto_sync():
    print("=" * 65)
    print("🚀 正在启动自动化数仓探查与元数据知识库同步引擎...")
    print("=" * 65)

    # 1. 实例化自动物理连接器 (直连 dw 数仓)
    connector = MySQLConnector(
        host=app_config.db_dw.host,
        port=app_config.db_dw.port,
        user=app_config.db_dw.user,
        password=app_config.db_dw.password,
        database=app_config.db_dw.database
    )

    # 2. 初始化目标存储客户端[cite: 1]
    meta_client_manager.init()
    embedding_client_manager.init()
    qdrant_client_manager.init()
    es_client_manager.init()

    try:
        async with meta_client_manager.session_factory() as meta_session:
            meta_mysql_repo = MetaMySQLRepository(meta_session)

            service = MetaKnowledgeService(
                connector=connector,
                meta_mysql_repository=meta_mysql_repo,
                embedding_client=embedding_client_manager.client,
                qdrant_client=qdrant_client_manager.client,
                es_client=es_client_manager.client,
                column_collection_name="data_agent_column",
                metric_collection_name="data_agent_metric",
                es_index_name="data_agent"
            )

            # 3. 执行全自动化流水线
            await service.auto_build_all(custom_metrics=DEFAULT_CORE_METRICS)
            await meta_session.commit()

    finally:
        # 4. 释放所有底层连接池
        await connector.close()
        await meta_client_manager.close()
        await qdrant_client_manager.close()
        await es_client_manager.close()
        print("=" * 65)
        print("✅ 数仓元数据探查与知识库构建顺利完成！")
        print("=" * 65)


if __name__ == '__main__':
    asyncio.run(run_auto_sync())