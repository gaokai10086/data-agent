"""初始化智能问数项目所需的 Meta 数据库表结构。"""
import asyncio

from sqlalchemy import text

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


async def _ensure_column_info_value_type(connection) -> bool:
    """为已存在的 column_info 表补 value_type 列；新表由 create_all 创建时已包含。"""
    exists = await connection.execute(
        text("""
            SELECT COUNT(*) FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = 'column_info'
              AND column_name = 'value_type'
        """)
    )
    if exists.scalar():
        return False
    await connection.execute(
        text("""
            ALTER TABLE column_info
            ADD COLUMN value_type VARCHAR(16) NOT NULL DEFAULT 'open'
            COMMENT '取值类型：closed=封闭枚举（人工维护值），open=开放枚举（模型推导）'
        """)
    )
    return True


async def init_meta_database() -> None:
    """幂等创建 Meta 表；已存在的表不会被删除或覆盖。"""
    print("🚀 正在初始化 Meta 数据库表结构...")
    meta_client_manager.init()
    try:
        if meta_client_manager.engine is None:
            raise RuntimeError("Meta 数据库连接池初始化失败")
        async with meta_client_manager.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            added = await _ensure_column_info_value_type(connection)
        print("✅ Meta 数据库表结构初始化完成。")
        if added:
            print("   已为 column_info 补充 value_type 列，历史数据默认为 open。")
        print(
            "   已确保存在：table_info、column_info、column_semantic_draft、table_relation、"
            "metric_info、column_metric、metric_example"
        )
    finally:
        await meta_client_manager.close()


if __name__ == "__main__":
    asyncio.run(init_meta_database())
