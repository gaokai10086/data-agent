"""修复城市取值资产：默认只展示计划，--apply 才更新 Meta 和对应索引。

仅处理 dim_store.city、dim_user.city 和 sales_amount 的城市维度。
实际值来自 DW；修改前保存快照，字段修改使用现有治理审计服务。
运行：python -m app.scripts.sync_city_value_metadata [--apply]
"""
import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import text
from qdrant_client import models

from app.clients.mysql_client import meta_client_manager, dw_client_manager
from app.clients.es_client import es_client_manager
from app.clients.qdrant_client import qdrant_client_manager
from app.core.meta.value_sampling_policy import MAX_ALLOWED_CARDINALITY
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository
from app.service.column_semantic_draft_service import ColumnSemanticDraftService
from app.service.value_index_rebuild_service import ValueIndexRebuildService

CITY_TABLES = ("dim_store", "dim_user")


async def sync_city_metadata(apply: bool, snapshot_dir: Path) -> None:
    meta_client_manager.init()
    dw_client_manager.init()
    es_client_manager.init()
    qdrant_client_manager.init()
    try:
        async with meta_client_manager.session_factory() as meta_session, dw_client_manager.session_factory() as dw_session:
            repository = MetaMySQLRepository(meta_session)
            selected = []
            plans = []
            before = {"fields": [], "qdrant_fields": [], "es_values": []}
            for table_name in CITY_TABLES:
                table = await repository.get_table_by_name(table_name)
                if table is None:
                    raise RuntimeError(f"Metadata 缺少 {table_name}")
                column = await repository.get_column(table.id, "city")
                if column is None:
                    raise RuntimeError(f"Metadata 缺少 {table_name}.city")
                # table_name 来自固定列表，不接受用户 SQL 输入。
                rows = await dw_session.execute(text(
                    f"SELECT DISTINCT city FROM {table_name} WHERE city IS NOT NULL AND city <> '' ORDER BY city LIMIT :cap"
                ), {"cap": MAX_ALLOWED_CARDINALITY + 1})
                values = [str(value).strip() for value in rows.scalars().all()]
                if not values or len(values) > MAX_ALLOWED_CARDINALITY:
                    raise RuntimeError(f"{table_name}.city 的非空值数量不适合本次受维护枚举配置")
                selected.append((table, column))
                plans.append({"column": f"{table_name}.city", "value_type": "closed", "examples": values})
                before["fields"].append({"id": column.id, "table": table_name,
                                         "value_type": column.value_type, "examples": column.examples})
                points, _ = await qdrant_client_manager.client.scroll(
                    collection_name="data_agent_column",
                    scroll_filter=models.Filter(must=[models.FieldCondition(
                        key="column_id", match=models.MatchValue(value=column.id))]),
                    limit=10, with_payload=True, with_vectors=False,
                )
                if len(points) != 1:
                    raise RuntimeError(f"{table_name}.city 的字段索引数量异常：{len(points)}")
                before["qdrant_fields"].append({"point_id": str(points[0].id), "payload": points[0].payload})
                hits = await es_client_manager.client.search(
                    index="data_agent", size=100,
                    query={"term": {"column_id": column.id}},
                )
                before["es_values"].extend(hits["hits"]["hits"])

            metric = await repository.get_metric_by_key("sales_amount")
            if metric is None:
                raise RuntimeError("Metadata 缺少 sales_amount")
            before["metric"] = {"id": metric.id, "dimensions": metric.dimensions, "version": metric.version}
            dimensions = list(metric.dimensions or [])
            if not any(d.get("column") == "dim_store.city" for d in dimensions):
                dimensions.append({"column": "dim_store.city", "name": "城市", "aliases": ["门店城市"]})
            print(json.dumps({"mode": "apply" if apply else "dry_run", "fields": plans,
                              "sales_amount_dimensions": dimensions}, ensure_ascii=False, indent=2))
            if not apply:
                return

            snapshot_dir.mkdir(parents=True, exist_ok=True)
            snapshot = snapshot_dir / f"city_assets_before_{datetime.now():%Y%m%d_%H%M%S}.json"
            snapshot.write_text(json.dumps(before, ensure_ascii=False, indent=2, default=str))
            print(f"修改前快照：{snapshot}")
            governance = ColumnSemanticDraftService(meta_repository=repository, connector=None, llm=None)
            for (_, column), plan in zip(selected, plans):
                if column.value_type != "closed" or list(column.examples or []) != plan["examples"]:
                    await governance.update_field(
                        column_id=column.id, reviewer="codex-user-authorized-city-fix",
                        review_comment="用户授权修复城市取值召回；登记 DW 实际城市值并同步相关索引",
                        value_type="closed", examples=plan["examples"],
                    )
            if metric.dimensions != dimensions:
                metric.dimensions = dimensions
                metric.version += 1
            await meta_session.commit()

            # 跨系统没有统一事务。Meta 是事实来源；索引失败后可重新运行本脚本同步。
            report = await ValueIndexRebuildService(repository, es_client_manager.client).sync_fields(selected)
            for (_, column), saved in zip(selected, before["qdrant_fields"]):
                await qdrant_client_manager.client.set_payload(
                    collection_name="data_agent_column", points=[saved["point_id"]],
                    payload={"examples": column.examples, "value_type": column.value_type}, wait=True,
                )
            await qdrant_client_manager.client.set_payload(
                collection_name="data_agent_metric",
                points=models.Filter(must=[models.FieldCondition(
                    key="metric_key", match=models.MatchValue(value="sales_amount"))]),
                payload={"dimensions": dimensions}, wait=True,
            )
            print(f"已同步两个城市字段，ES 文档数：{report.indexed_values}；其他字段未重建")
    finally:
        await asyncio.gather(meta_client_manager.close(), dw_client_manager.close(),
                             es_client_manager.close(), qdrant_client_manager.close())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--snapshot-dir", type=Path, default=Path("/private/tmp/data-agent-city-snapshots"))
    args = parser.parse_args()
    asyncio.run(sync_city_metadata(args.apply, args.snapshot_dir))
