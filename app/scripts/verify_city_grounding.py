"""用普通 Data Agent 入口对照人工 SQL，验证城市、大区、门店名与用户城市。

不经过异动 Agent；全部 SQL 只读。默认运行四题，--case beijing 可只跑原问题。
"""
import argparse
import asyncio
import json
import re
import time
from decimal import Decimal
from pathlib import Path

from app.agent.data_agent.graph import graph
from app.clients.embedding_client import embedding_client_manager
from app.clients.es_client import es_client_manager
from app.clients.mysql_client import dw_client_manager, meta_client_manager
from app.clients.qdrant_client import qdrant_client_manager
from app.repositories.es.value_es_repository import ValueESRepository
from app.repositories.mysql.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository
from app.repositories.qdrant.column_repository_qdrant import ColumnQdrantRepository
from app.repositories.qdrant.metric_repository_qdrant import MetricQdrantRepository
from app.service.sql_validation_service import SQLSafetyPolicy, SQLValidationService, TABLE_ALIAS_PATTERN, RESERVED_ALIAS_WORDS


CASES = [
    {"id": "beijing", "query": "查询2025年北京市7月的销售额", "table": "dim_store", "field": "city", "value": "北京"},
    {"id": "region", "query": "查询2025年华北大区7月的销售额", "table": "dim_store", "field": "region", "value": "华北"},
    {"id": "store", "query": "查询2025年北京王府井旗舰店7月的销售额", "table": "dim_store", "field": "store_name", "value": "北京王府井旗舰店"},
    {"id": "user_city", "query": "查询2025年7月用户所在城市为北京市的销售额", "table": "dim_user", "field": "city", "value": "北京"},
]


def scalar(rows):
    if not isinstance(rows, list) or len(rows) != 1 or len(rows[0]) != 1:
        raise ValueError(f"总体指标应返回一行一列，实际为 {rows!r}")
    value = next(iter(rows[0].values()))
    return None if value is None else Decimal(str(value))


def verifies_filter(sql: str, case: dict) -> bool:
    if case["field"] == "city":
        return not SQLValidationService.validate_grounded_city_values(sql, [{
            "table_name": case["table"], "column_name": "city", "value": case["value"],
        }])
    aliases = {case["table"]}
    for match in TABLE_ALIAS_PATTERN.finditer(sql):
        table, alias = match.groups()
        if table == case["table"] and alias and alias.lower() not in RESERVED_ALIAS_WORDS:
            aliases.add(alias)
    qualifier = "|".join(re.escape(alias) for alias in aliases)
    pattern = rf"`?(?:{qualifier})`?\.`?{case['field']}`?\s*(?:=|LIKE)\s*['\"]%?{re.escape(case['value'])}%?['\"]"
    return bool(re.search(pattern, sql, re.IGNORECASE))


async def verify(case_id: str | None, report_path: Path):
    meta_client_manager.init()
    dw_client_manager.init()
    qdrant_client_manager.init()
    es_client_manager.init()
    embedding_client_manager.init()
    reports = []
    try:
        for case in CASES:
            if case_id and case["id"] != case_id:
                continue
            started = time.perf_counter()
            report = {"id": case["id"], "query": case["query"], "passed": False}
            print(f"开始验证：{case['query']}", flush=True)
            try:
                async with meta_client_manager.session_factory() as meta, dw_client_manager.session_factory() as dw:
                    repository = DWMySQLRepository(dw)
                    baseline = (
                        "SELECT COALESCE(SUM(o.pay_amount),0) AS value FROM fact_order o "
                        f"JOIN {case['table']} d ON o.{'user_id' if case['table'] == 'dim_user' else 'store_id'}"
                        f" = d.{'user_id' if case['table'] == 'dim_user' else 'store_id'} "
                        "WHERE o.order_status='已完成' AND o.pay_time >= '2025-07-01' "
                        "AND o.pay_time < '2025-08-01' "
                        f"AND d.{case['field']} = :filter_value"
                    )
                    from sqlalchemy import text
                    gold_rows = (await dw.execute(text(baseline), {"filter_value": case["value"]})).mappings().all()
                    expected = scalar([dict(row) for row in gold_rows])

                    # 测试入口限制执行只读 SQL；仍调用应用现有 Repository。
                    execute = repository.execute_sql
                    async def safe_execute(sql):
                        SQLSafetyPolicy.ensure_read_only_sql(sql)
                        return await execute(sql)
                    repository.execute_sql = safe_execute

                    state = await graph.ainvoke({"query": case["query"]}, context={
                        "meta_mysql_repository": MetaMySQLRepository(meta), "dw_mysql_repository": repository,
                        "dw_session_factory": dw_client_manager.session_factory,
                        "column_qdrant_repository": ColumnQdrantRepository(qdrant_client_manager.client),
                        "metric_qdrant_repository": MetricQdrantRepository(qdrant_client_manager.client),
                        "value_es_repository": ValueESRepository(es_client_manager.client),
                        "embedding_client": embedding_client_manager.client,
                    })
                    actual = scalar(state.get("sql_result"))
                    filter_verified = verifies_filter(state.get("sql") or "", case)
                    report.update(sql=state.get("sql"), expected=expected, actual=actual,
                                  values=state.get("retrieved_values"), retry_count=state.get("retry_count", 0),
                                  filter_verified=filter_verified,
                                  passed=filter_verified and actual == expected and not state.get("error_response"))
            except Exception as exc:
                report["error"] = str(exc)
            report["latency_ms"] = round((time.perf_counter() - started) * 1000)
            reports.append(report)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(reports, ensure_ascii=False, indent=2, default=str))
            print(json.dumps(report, ensure_ascii=False, default=str), flush=True)
    finally:
        await asyncio.gather(meta_client_manager.close(), dw_client_manager.close(),
                             qdrant_client_manager.close(), es_client_manager.close())
    if not reports or not all(r["passed"] for r in reports):
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=[c["id"] for c in CASES])
    parser.add_argument("--report", type=Path, default=Path("docs/diagnostics/city_grounding_regression.json"))
    args = parser.parse_args()
    asyncio.run(verify(args.case, args.report))
