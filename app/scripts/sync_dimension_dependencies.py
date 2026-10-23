"""为现有销售额登记城市→大区依赖；验证实际数据、保存快照、同步指标索引。

默认预览，--apply 写入。只更新这一条指标的 dimensions JSON，不修改 DW。
"""
import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

from qdrant_client import models

from app.clients.mysql_client import meta_client_manager, dw_client_manager
from app.clients.qdrant_client import qdrant_client_manager
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository
from app.repositories.mysql.dw_mysql_repository import DWMySQLRepository
from app.service.dimension_dependency_service import validate_dependency_data


async def sync(apply: bool = False) -> None:
    meta_client_manager.init()
    dw_client_manager.init()
    qdrant_client_manager.init()
    try:
        async with meta_client_manager.session_factory() as session, dw_client_manager.session_factory() as dw_session:
            metric = await MetaMySQLRepository(session).get_metric_by_key("sales_amount")
            if metric is None:
                raise ValueError("Metadata 中缺少销售额指标")
            dimensions = [dict(item) for item in metric.dimensions or []]
            city = next((item for item in dimensions if item["column"] == "dim_store.city"), None)
            if city is None:
                raise ValueError("销售额 Metadata 缺少城市维度")
            city["determines"] = list(dict.fromkeys([*city.get("determines", []), "dim_store.region"]))
            await validate_dependency_data(dimensions, DWMySQLRepository(dw_session))
            print(json.dumps({"apply": apply, "dependency_data_valid": True, "dimensions": dimensions},
                             ensure_ascii=False, indent=2))
            if not apply:
                return
            # Meta 是事实来源；跨系统同步失败可重跑本脚本补齐 Qdrant。
            if dimensions != metric.dimensions:
                snapshot = Path("/private/tmp") / f"sales_dimension_dependencies_before_{datetime.now():%Y%m%d_%H%M%S_%f}.json"
                snapshot.write_text(json.dumps({"metric_key": metric.metric_key, "version": metric.version,
                                               "dimensions": metric.dimensions}, ensure_ascii=False, indent=2))
                print(f"修改前快照：{snapshot}")
                metric.dimensions = dimensions
                metric.version += 1
                await session.commit()
            points, _ = await qdrant_client_manager.client.scroll(
                collection_name="data_agent_metric", limit=2, with_vectors=False,
                scroll_filter=models.Filter(must=[models.FieldCondition(
                    key="metric_key", match=models.MatchValue(value="sales_amount"))]),
            )
            if len(points) != 1:
                raise RuntimeError("Meta 已保存，但销售额索引数量异常；修复索引后重跑脚本")
            await qdrant_client_manager.client.set_payload(
                collection_name="data_agent_metric", points=[points[0].id],
                payload={"dimensions": dimensions, "version": metric.version}, wait=True,
            )
            print("城市→大区依赖已登记，指标索引已同步。")
    finally:
        await asyncio.gather(meta_client_manager.close(), dw_client_manager.close(), qdrant_client_manager.close())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    asyncio.run(sync(parser.parse_args().apply))
