"""发布销售额与订单数量两条初始业务指标。"""
import asyncio

from app.clients.mysql_client import meta_client_manager
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository
from app.service.business_asset_seed_service import BusinessAssetSeedService


async def seed_initial_business_assets() -> None:
    print("=" * 72)
    print("🚀 开始发布初始业务资产：销售额、订单数量")
    print("=" * 72)
    meta_client_manager.init()
    try:
        if meta_client_manager.session_factory is None:
            raise RuntimeError("Meta 数据库连接池初始化失败")
        async with meta_client_manager.session_factory() as session:
            service = BusinessAssetSeedService(MetaMySQLRepository(session))
            report = await service.seed()
            await session.commit()
        print("✅ 初始业务资产发布完成：")
        print(f"   指标：新增 {report.metrics_created}，口径更新 {report.metrics_updated}")
        print(f"   公式血缘：新增 {report.lineages_created}，已存在 {report.lineages_existing}")
        print(f"   指标示例：新增 {report.examples_created}，已存在 {report.examples_existing}")
    finally:
        await meta_client_manager.close()


if __name__ == "__main__":
    asyncio.run(seed_initial_business_assets())
