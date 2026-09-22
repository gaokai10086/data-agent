"""删除并重建 Qdrant 指标索引，使其与 Meta 中已发布指标一致。"""
import asyncio

from app.clients.embedding_client import embedding_client_manager
from app.clients.mysql_client import meta_client_manager
from app.clients.qdrant_client import qdrant_client_manager
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository
from app.service.metric_index_rebuild_service import MetricIndexRebuildService


async def rebuild_metric_index() -> None:
    print("🚀 开始删除旧指标索引并重建...")
    meta_client_manager.init()
    embedding_client_manager.init()
    qdrant_client_manager.init()
    try:
        if meta_client_manager.session_factory is None:
            raise RuntimeError("Meta 数据库连接池初始化失败")
        async with meta_client_manager.session_factory() as session:
            service = MetricIndexRebuildService(
                meta_repository=MetaMySQLRepository(session),
                embedding_client=embedding_client_manager.client,
                qdrant_client=qdrant_client_manager.client,
            )
            report = await service.rebuild()
        print(f"✅ 指标索引重建完成，共索引 {report.metrics_indexed} 个已发布指标。")
    finally:
        await meta_client_manager.close()
        await qdrant_client_manager.close()


if __name__ == "__main__":
    asyncio.run(rebuild_metric_index())
