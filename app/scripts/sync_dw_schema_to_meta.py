"""DW → Meta Schema 同步入口：同步真实表、字段、主外键和 Join 关系。"""
import asyncio

from app.clients.mysql_client import meta_client_manager
from app.config.app_config import app_config
from app.core.datasource.mysql_connector import MySQLConnector
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository
from app.service.schema_sync_service import SchemaSyncService


async def sync_dw_schema_to_meta() -> None:
    print("=" * 72)
    print(f"🚀 开始同步 DW [{app_config.db_dw.database}] 的物理 Schema 到 Meta 数据库...")
    print("=" * 72)
    connector = MySQLConnector(
        host=app_config.db_dw.host,
        port=app_config.db_dw.port,
        user=app_config.db_dw.user,
        password=app_config.db_dw.password,
        database=app_config.db_dw.database,
    )
    meta_client_manager.init()
    try:
        if meta_client_manager.session_factory is None:
            raise RuntimeError("Meta 数据库连接池初始化失败")
        async with meta_client_manager.session_factory() as session:
            service = SchemaSyncService(connector, MetaMySQLRepository(session))
            report = await service.sync()
            await session.commit()
        print("✅ DW Schema 同步完成：")
        print(f"   表：新增 {report.tables_created}，补全 {report.tables_updated}")
        print(f"   字段：新增 {report.columns_created}，更新 {report.columns_updated}")
        print(f"   Join 关系：新增 {report.relations_created}，已存在 {report.relations_existing}")
    finally:
        await connector.close()
        await meta_client_manager.close()


if __name__ == "__main__":
    asyncio.run(sync_dw_schema_to_meta())
