"""定向登记销售额拆解。默认只读预览，--apply 补列并保存快照后更新销售额。

不重建指标索引，仅同步已有销售额点的 payload；MySQL 是权威来源。
"""
import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from qdrant_client import models
from sqlalchemy import text

from app.clients.mysql_client import meta_client_manager
from app.clients.qdrant_client import qdrant_client_manager
from app.config.app_config import app_config
from app.schemas.metric_decomposition import sales_amount_decomposition
from app.scripts.init_meta_database import _ensure_metric_info_decomposition
from app.service.metric_decomposition_service import validate_metric_decomposition


async def migrate(connection, *, apply: bool = False, snapshot_dir: Path = Path("/private/tmp")) -> dict:
    exists = bool((await connection.execute(text("""
        SELECT COUNT(*) FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'metric_info'
          AND column_name = 'decomposition'
    """))).scalar())
    # 旧表尚未补列时使用 NULL 投影，避免新 ORM 查询先因缺列失败。
    column = "decomposition" if exists else "NULL AS decomposition"
    result = await connection.execute(text(f"""
        SELECT id, metric_key, name, formula, fact_table, measure_column, aggregation,
               time_column, filters, status, version, {column}
        FROM metric_info WHERE metric_key IN (:sales, :orders)
    """), {"sales": "sales_amount", "orders": "order_count"})
    records = {row["metric_key"]: dict(row) for row in result.mappings()}
    for record in records.values():
        for field in ("filters", "decomposition"):
            if isinstance(record[field], str):
                record[field] = json.loads(record[field])
    sales = records.get("sales_amount")
    if sales is None or sales["status"] != "published":
        raise ValueError("缺少已发布销售额指标")

    async def get_metric_by_key(key):
        record = records.get(key)
        return SimpleNamespace(**record) if record else None

    config = sales_amount_decomposition()
    await validate_metric_decomposition(SimpleNamespace(**sales), config,
                                        SimpleNamespace(get_metric_by_key=get_metric_by_key))
    desired = config.model_dump(mode="json")
    if sales["decomposition"] is not None and sales["decomposition"] != desired:
        raise ValueError("销售额已有不同的人工拆解配置，拒绝覆盖，请先审核现有配置")
    changed = sales["decomposition"] != desired
    report = {"apply": apply, "column_will_be_added": not exists, "metric_will_change": changed,
              "metric_key": "sales_amount", "version": sales["version"],
              "decomposition": desired, "formula_preserved": sales["formula"]}
    if not apply:
        return report
    if changed:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        snapshot = snapshot_dir / f"sales_decomposition_before_{datetime.now():%Y%m%d_%H%M%S_%f}.json"
        snapshot.write_text(json.dumps({"column_existed": exists, "metric": sales},
                                       ensure_ascii=False, indent=2), encoding="utf-8")
        report["snapshot"] = str(snapshot)
    await _ensure_metric_info_decomposition(connection)
    if changed:
        updated = await connection.execute(text("""
            UPDATE metric_info SET decomposition = :config, version = version + 1
            WHERE metric_key = :key AND version = :version AND decomposition IS NULL
        """), {"config": json.dumps(desired, ensure_ascii=False), "key": "sales_amount",
                 "version": sales["version"]})
        if updated.rowcount != 1:
            raise RuntimeError("指标已被其他操作修改，本次停止，请重新预览")
        report["version"] += 1
    report["metric_id"] = sales["id"]
    return report


async def sync_index(report: dict) -> None:
    qdrant_client_manager.init()
    try:
        client = qdrant_client_manager.client
        collection = app_config.qdrant.metric_collection
        points, _ = await client.scroll(
            collection_name=collection, limit=2, with_vectors=False,
            scroll_filter=models.Filter(must=[models.FieldCondition(
                key="metric_key", match=models.MatchValue(value="sales_amount"))]),
        )
        if len(points) != 1 or str(points[0].payload.get("id")) != str(report["metric_id"]):
            raise RuntimeError("销售额索引数量或指标 ID 不一致，拒绝更新错误索引")
        await client.set_payload(collection_name=collection, points=[points[0].id],
                                 payload={"decomposition": report["decomposition"],
                                          "version": report["version"]}, wait=True)
    finally:
        await qdrant_client_manager.close()


async def sync(apply: bool = False) -> None:
    meta_client_manager.init()
    try:
        async with meta_client_manager.engine.begin() as connection:
            report = await migrate(connection, apply=apply)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if apply:
            try:
                await sync_index(report)
            except Exception as exc:
                raise RuntimeError("MySQL 拆解配置已保存，但 Qdrant 同步失败；修复连接后重跑本脚本补齐索引") from exc
            print("销售额拆解配置已保存，Qdrant payload 已同步；未开启自动指标拆解执行。")
    finally:
        await meta_client_manager.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    asyncio.run(sync(parser.parse_args().apply))
