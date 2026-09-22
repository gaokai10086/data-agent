"""重建 Elasticsearch 枚举值索引，供 Value Recall 精准匹配真实业务值。"""
import asyncio

from app.clients.es_client import es_client_manager
from app.clients.mysql_client import meta_client_manager
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository
from app.service.value_index_rebuild_service import ValueIndexRebuildService


async def rebuild_value_index() -> None:
    print("🚀 开始重建 Elasticsearch 枚举值索引...")
    meta_client_manager.init()
    es_client_manager.init()
    try:
        if meta_client_manager.session_factory is None:
            raise RuntimeError("Meta 数据库连接池初始化失败")
        async with meta_client_manager.session_factory() as session:
            service = ValueIndexRebuildService(
                meta_repository=MetaMySQLRepository(session),
                es_client=es_client_manager.client,
            )
            report = await service.rebuild()
        print(f"✅ 枚举值索引重建完成，共写入 {report.indexed_values} 个值。")
        if report.skipped_columns:
            print(f"   按安全策略跳过 {len(report.skipped_columns)} 个字段。")
    finally:
        await meta_client_manager.close()
        await es_client_manager.close()


if __name__ == "__main__":
    asyncio.run(rebuild_value_index())
