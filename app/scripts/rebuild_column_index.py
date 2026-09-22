"""重建 Qdrant 字段语义索引，使其与当前 Meta 的字段 ID 和语义保持一致。"""
import asyncio

from app.clients.embedding_client import embedding_client_manager
from app.clients.mysql_client import meta_client_manager
from app.clients.qdrant_client import qdrant_client_manager
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository
from app.service.column_index_rebuild_service import ColumnIndexRebuildService


async def rebuild_column_index() -> None:
    print("🚀 开始重建 Qdrant 字段语义索引...")
    meta_client_manager.init()
    embedding_client_manager.init()
    qdrant_client_manager.init()
    try:
        if meta_client_manager.session_factory is None:
            raise RuntimeError("Meta 数据库连接池初始化失败")
        async with meta_client_manager.session_factory() as session:
            service = ColumnIndexRebuildService(
                meta_repository=MetaMySQLRepository(session),
                embedding_client=embedding_client_manager.client,
                qdrant_client=qdrant_client_manager.client,
            )
            report = await service.rebuild()
        print(f"✅ 字段语义索引重建完成，共索引 {report.columns_indexed} 个字段。")
    finally:
        await meta_client_manager.close()
        await qdrant_client_manager.close()


if __name__ == "__main__":
    asyncio.run(rebuild_column_index())
