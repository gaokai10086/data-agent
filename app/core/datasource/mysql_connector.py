"""
================================================================================
模块名称：MySQL 物理反射连接器 (借鉴 DB-GPT MySQLConnector)
模块路径：app/core/datasource/mysql_connector.py
核心职责：
    1. 基于 SQLAlchemy + asyncmy 异步并发探查数仓元数据
    2. 无侵入扫描 information_schema 字典表
    3. 自动提取表/字段注释并动态抽样代表性枚举数据
================================================================================
"""
from typing import Any
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text

from app.core.datasource.base import (
    BaseDatasourceConnector,
    ColumnProfile,
    ForeignKeyProfile,
    TableProfile,
)
from app.core.logging import logger


class MySQLConnector(BaseDatasourceConnector):
    def __init__(self, host: str, port: int, user: str, password: str, database: str):
        self.database = database
        self.db_url = f"mysql+asyncmy://{user}:{password}@{host}:{port}/{database}?charset=utf8mb4"
        self.engine = create_async_engine(
            self.db_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10
        )
        self.session_factory = sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False
        )

    async def get_all_table_names(self) -> list[str]:
        """自动从 information_schema 扫描全部物理基础表"""
        async with self.session_factory() as session:
            sql = text("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = :db AND table_type = 'BASE TABLE'
                ORDER BY table_name
            """)
            result = await session.execute(sql, {"db": self.database})
            tables = [row[0] for row in result.fetchall()]
            logger.info(f"🔍 [MySQLConnector] 探测到数仓 [{self.database}] 共有物理表: {tables}")
            return tables

    async def get_table_profile(self, table_name: str) -> TableProfile:
        """通过字典表提取指定表的注释与字段画像"""
        async with self.session_factory() as session:
            # 1. 查询表注释说明
            tbl_sql = text("""
                SELECT table_comment 
                FROM information_schema.tables 
                WHERE table_schema = :db AND table_name = :tbl
            """)
            tbl_res = await session.execute(tbl_sql, {"db": self.database, "tbl": table_name})
            row = tbl_res.fetchone()
            table_comment = row[0] if row and row[0] else table_name

            # 2. 查询字段级详细属性
            col_sql = text("""
                SELECT 
                    column_name, 
                    data_type, 
                    is_nullable, 
                    column_key, 
                    column_comment
                FROM information_schema.columns 
                WHERE table_schema = :db AND table_name = :tbl
                ORDER BY ordinal_position
            """)
            col_res = await session.execute(col_sql, {"db": self.database, "tbl": table_name})

            columns = []
            for col in col_res.fetchall():
                c_name, c_type, c_null, c_key, c_comment = col
                columns.append(ColumnProfile(
                    name=c_name,
                    data_type=c_type.upper(),
                    is_nullable=(c_null == "YES"),
                    is_primary_key=(c_key == "PRI"),
                    comment=c_comment if c_comment else c_name,
                    sample_values=[]
                ))

            return TableProfile(
                table_name=table_name,
                comment=table_comment,
                columns=columns
            )

    async def sample_column_values(self, table_name: str, column_name: str, limit: int = 15) -> list[Any]:
        """非空去重采样真实物理值"""
        async with self.session_factory() as session:
            try:
                # 采样非空真实值，防止大模型出现拼写幻觉
                sample_sql = text(f"""
                    SELECT DISTINCT `{column_name}` 
                    FROM `{table_name}` 
                    WHERE `{column_name}` IS NOT NULL AND `{column_name}` != ''
                    LIMIT :limit
                """)
                res = await session.execute(sample_sql, {"limit": limit})
                return [row[0] for row in res.fetchall()]
            except Exception as e:
                logger.warning(f"⚠️ 字段采样异常 [{table_name}.{column_name}]: {e}")
                return []

    async def get_create_table_ddl(self, table_name: str) -> str:
        """获取真实的建表 DDL"""
        async with self.session_factory() as session:
            res = await session.execute(text(f"SHOW CREATE TABLE `{table_name}`"))
            row = res.fetchone()
            return row[1] if row else ""

    async def probe_distinct_value_count(
        self, table_name: str, column_name: str, cap: int = 31
    ) -> int:
        """有上限地探测去重取值数量。

        子查询带 LIMIT，MySQL 在凑满 cap 行后即可停止，避免对大表执行全量
        COUNT(DISTINCT)。返回 min(真实去重数量, cap)。
        """
        async with self.session_factory() as session:
            probe_sql = text(f"""
                SELECT COUNT(*) FROM (
                    SELECT `{column_name}` AS v
                    FROM `{table_name}`
                    WHERE `{column_name}` IS NOT NULL AND `{column_name}` != ''
                    GROUP BY `{column_name}`
                    LIMIT :cap
                ) AS bounded_distinct
            """)
            res = await session.execute(probe_sql, {"cap": cap})
            return int(res.scalar() or 0)

    async def get_frequent_column_values(
        self, table_name: str, column_name: str, limit: int = 12
    ) -> list[Any]:
        """按频次采样字段的高频真实取值，供值召回与枚举展示使用。"""
        async with self.session_factory() as session:
            sample_sql = text(f"""
                SELECT `{column_name}` AS v, COUNT(*) AS freq
                FROM `{table_name}`
                WHERE `{column_name}` IS NOT NULL AND `{column_name}` != ''
                GROUP BY `{column_name}`
                ORDER BY freq DESC, `{column_name}` ASC
                LIMIT :limit
            """)
            res = await session.execute(sample_sql, {"limit": limit})
            return [row[0] for row in res.fetchall()]

    async def get_foreign_keys(self) -> list[ForeignKeyProfile]:
        """从 information_schema 读取当前 DW 的字段级外键关系。"""
        async with self.session_factory() as session:
            sql = text("""
                SELECT
                    kcu.table_name AS source_table,
                    kcu.column_name AS source_column,
                    kcu.referenced_table_name AS target_table,
                    kcu.referenced_column_name AS target_column,
                    kcu.constraint_name
                FROM information_schema.key_column_usage AS kcu
                WHERE kcu.table_schema = :db
                  AND kcu.referenced_table_schema = :db
                  AND kcu.referenced_table_name IS NOT NULL
                ORDER BY kcu.table_name, kcu.constraint_name, kcu.ordinal_position
            """)
            result = await session.execute(sql, {"db": self.database})
            foreign_keys = [
                ForeignKeyProfile(
                    # information_schema 的字段名在不同 MySQL 驱动中可能被规范化为大写，
                    # 因此这里按 SELECT 列顺序取值，避免受返回字段名大小写影响。
                    source_table=row[0],
                    source_column=row[1],
                    target_table=row[2],
                    target_column=row[3],
                    constraint_name=row[4],
                )
                for row in result.fetchall()
            ]
            logger.info(f"🔗 [MySQLConnector] 探测到数仓 [{self.database}] 外键关系: {len(foreign_keys)} 条")
            return foreign_keys

    async def close(self):
        """平滑释放连接池"""
        await self.engine.dispose()

# ==================== 本地独立自测验证 ====================
if __name__ == '__main__':
    import asyncio
    from app.config.app_config import app_config

    async def test_connector():
        connector = MySQLConnector(
            host=app_config.db_dw.host,
            port=app_config.db_dw.port,
            user=app_config.db_dw.user,
            password=app_config.db_dw.password,
            database=app_config.db_dw.database
        )
        try:
            print("🚀 开始测试 MySQL 连接器自动探查能力...")
            tables = await connector.get_all_table_names()
            for tbl in tables:
                profile = await connector.get_table_profile(tbl)
                print(f"\n📦 表名: {profile.table_name} | 注释: {profile.comment}")
                for col in profile.columns:
                    # 尝试对字符字段抽样
                    if "CHAR" in col.data_type:
                        samples = await connector.sample_column_values(tbl, col.name, limit=3)
                        col.sample_values = samples
                    print(f"  └─ 字段: {col.name:<15} 类型: {col.data_type:<10} 主键: {col.is_primary_key:<5} 注释: {col.comment} 样例: {col.sample_values}")
            print("\n✅ 连接器探查测试圆满成功！")
        finally:
            await connector.close()

    asyncio.run(test_connector())
