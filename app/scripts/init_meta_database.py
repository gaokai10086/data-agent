"""初始化智能问数项目所需的 Meta 数据库表结构。"""
import asyncio

from app.clients.mysql_client import meta_client_manager
from app.models.mysql import (  # noqa: F401，导入以注册全部 ORM 模型
    ColumnInfoMySQL,
    ColumnMetricMySQL,
    ColumnSemanticDraftMySQL,
    MetricExampleMySQL,
    MetricInfoMySQL,
    TableInfoMySQL,
    TableRelationMySQL,
)
from app.models.mysql.base import Base


async def init_meta_database() -> None:
    """幂等创建 Meta 表；已存在的表不会被删除或覆盖。"""
    print("🚀 正在初始化 Meta 数据库表结构...")
    meta_client_manager.init()
    try:
        if meta_client_manager.engine is None:
            raise RuntimeError("Meta 数据库连接池初始化失败")
        async with meta_client_manager.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        print("✅ Meta 数据库表结构初始化完成。")
        print(
            "   已确保存在：table_info、column_info、column_semantic_draft、table_relation、"
            "metric_info、column_metric、metric_example"
        )
    finally:
        await meta_client_manager.close()


if __name__ == "__main__":
    asyncio.run(init_meta_database())
