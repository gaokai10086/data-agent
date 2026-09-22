"""
================================================================================
模块名称：异步 MySQL 客户端连接池与会话生命周期管理器 🗄️
文件路径：app/clients/mysql_client.py
核心改进：
    1. 彻底剔除重复粘贴的冗余代码与重复执行块
    2. 对齐 DatabaseConfig 驱动协议，统一采用 mysql+asyncmy 异步引擎
    3. 封装 get_session() 异步上下文管理器，支持自动 Commit、Rollback 与连接归还
================================================================================
"""
import asyncio
from typing import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
    AsyncEngine
)

from app.config.app_config import DatabaseConfig, app_config


class MySQLClientManager:
    def __init__(self, db_config: DatabaseConfig):
        self.db_config = db_config
        self.engine: AsyncEngine | None = None
        self.session_factory: async_sessionmaker[AsyncSession] | None = None

    def _get_url(self) -> str:
        """组装 SQLAlchemy 2.0 标准 asyncmy 异步连接串"""
        return (
            f"mysql+asyncmy://{self.db_config.user}:{self.db_config.password}@"
            f"{self.db_config.host}:{self.db_config.port}/{self.db_config.database}"
            f"?charset=utf8mb4"
        )

    def init(self):
        """初始化异步引擎连接池与会话工厂"""
        self.engine = create_async_engine(
            self._get_url(),
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,  # 自动探活，防止数据库闲置超时丢弃连接
            pool_recycle=3600,  # 1小时周期性回收连接，防 MySQL wait_timeout
            echo=False  # 生产环境关闭原始 SQL 打印，避免日志膨胀
        )
        self.session_factory = async_sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False
        )

    @asynccontextmanager
    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """
        核心上下文管理器：供 Service 与 Repository 优雅调用
        特性：自动提交、异常自动回滚、离开上下文自动归还连接池
        """
        if self.session_factory is None:
            raise RuntimeError(f"❌ 数据库 [{self.db_config.database}] 尚未初始化，请先调用 init()")

        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    async def close(self):
        """优雅销毁连接池"""
        if self.engine:
            await self.engine.dispose()


# 实例化全局单例对象
dw_client_manager = MySQLClientManager(app_config.db_dw)
meta_client_manager = MySQLClientManager(app_config.db_meta)

if __name__ == '__main__':
    async def probe_database(name: str, client_manager: MySQLClientManager):
        """执行最小只读查询，确认指定数据库可连接且当前账号可访问。"""
        async with client_manager.get_session() as session:
            result = await session.execute(text("SHOW TABLES;"))
            tables = result.scalars().all()
            print(f"✅ {name} 连接成功！现有物理表列表: {tables}")


    async def main():
        print("🚀 开始自检 MySQL 异步连接池：DW 数仓库 + Meta 元数据库...")

        # 1. 两个 Client 都必须先创建连接池；init() 本身通常不会发起真实网络连接。
        dw_client_manager.init()
        meta_client_manager.init()

        try:
            # 2. 使用最小只读查询分别验证两套库的网络连接、账号权限和库名配置。
            await probe_database("DW 数仓库", dw_client_manager)
            await probe_database("Meta 元数据库", meta_client_manager)
        finally:
            # 3. 无论任一库探活成功或失败，都释放两个连接池，避免本地自检残留连接。
            await dw_client_manager.close()
            await meta_client_manager.close()

        print("🎉 双库自检完成，两个连接池均已安全释放！")


    asyncio.run(main())
