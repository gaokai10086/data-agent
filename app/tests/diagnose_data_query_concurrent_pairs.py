"""真实 DataQueryTool 并发双周期稳定性测试。"""
from __future__ import annotations
import asyncio, json, time, traceback, uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from app.agent.anomaly_agent.common.data_query.schemas import DataQueryRequest
from app.agent.anomaly_agent.common.data_query.tool import DataQueryTool
from app.clients.embedding_client import embedding_client_manager
from app.clients.es_client import es_client_manager
from app.clients.mysql_client import dw_client_manager, meta_client_manager
from app.clients.qdrant_client import qdrant_client_manager
from app.config.app_config import DEFAULT_LLM_MODEL, DEFAULT_LLM_PROVIDER
from app.repositories.es.value_es_repository import ValueESRepository
from app.repositories.mysql.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository
from app.repositories.qdrant.column_repository_qdrant import ColumnQdrantRepository
from app.repositories.qdrant.metric_repository_qdrant import MetricQdrantRepository

OUT = Path(__file__).resolve().parents[2] / "diagnostics" / "data_query_concurrent_pairs_5.jsonl"
EXPECTED = {"2026-07": Decimal("233378.95"), "2026-08": Decimal("120953.99")}
TIMEOUT = 90.0

async def query_one(tool, context, group, period):
    trace = f"concurrent-pair-{group}-{period}-{uuid.uuid4().hex[:8]}"
    events = []
    start = time.monotonic()
    runtime = SimpleNamespace(context=dict(context), stream_writer=lambda e: events.append({"at": datetime.now().isoformat(), **(e if isinstance(e, dict) else {"event": str(e)})}))
    req = DataQueryRequest(query=f"查询 2026 年 {7 if period == '2026-07' else 8} 月北京地区销售额，不按维度分组，只返回 value。", request_id=trace, purpose="concurrent_pair_diagnostic", role=period, period=period, metric_key="sales_amount", expected_columns=["value"], max_rows=1)
    rec = {"group": group, "period": period, "trace_id": trace, "started_at": datetime.now().isoformat(), "events": events, "_start_mono": start}
    try:
        result = await asyncio.wait_for(tool.run(req, runtime), timeout=TIMEOUT)
        value = result.rows[0].get("value") if result.rows else None
        rec.update(status="success" if result.success and Decimal(str(value)) == EXPECTED[period] else "business_failure", success=result.success, value=str(value) if value is not None else None, sql=result.sql, error=result.error_message)
    except asyncio.CancelledError:
        rec.update(status="cancelled", exception_type="CancelledError", traceback=traceback.format_exc())
        raise
    except asyncio.TimeoutError:
        rec.update(status="timeout", exception_type="TimeoutError", traceback=traceback.format_exc())
    except Exception as exc:
        rec.update(status="exception", exception_type=type(exc).__name__, error=str(exc), traceback=traceback.format_exc())
    finally:
        rec.update(ended_at=datetime.now().isoformat(), elapsed_ms=round((time.monotonic() - start) * 1000, 1), unfinished_tasks=sum(1 for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()), _end_mono=time.monotonic())
    return rec

async def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    for m in (meta_client_manager, dw_client_manager, es_client_manager, qdrant_client_manager, embedding_client_manager): m.init()
    try:
        async with meta_client_manager.session_factory() as ms, dw_client_manager.session_factory() as ds:
            context = {"metric_qdrant_repository": MetricQdrantRepository(qdrant_client_manager.client), "column_qdrant_repository": ColumnQdrantRepository(qdrant_client_manager.client), "value_es_repository": ValueESRepository(es_client_manager.client), "embedding_client": embedding_client_manager.client, "meta_mysql_repository": MetaMySQLRepository(ms), "dw_mysql_repository": DWMySQLRepository(ds), "meta_session_factory": meta_client_manager.session_factory, "dw_session_factory": dw_client_manager.session_factory, "llm_provider": DEFAULT_LLM_PROVIDER, "llm_model_tier": DEFAULT_LLM_MODEL}
            tool = DataQueryTool()
            with OUT.open("w", encoding="utf-8") as f:
                for group in range(1, 6):
                    group_start = time.monotonic()
                    july, august = await asyncio.gather(query_one(tool, context, group, "2026-07"), query_one(tool, context, group, "2026-08"))
                    # Let LangGraph cancellation/finalizers run before assessing
                    # whether this pair left background tasks behind.
                    await asyncio.sleep(0)
                    pending_after_group = [
                        t.get_name() for t in asyncio.all_tasks()
                        if t is not asyncio.current_task() and not t.done()
                    ]
                    overlap = max(0.0, min(july["_end_mono"], august["_end_mono"]) - max(july["_start_mono"], august["_start_mono"]))
                    july.pop("_start_mono", None); july.pop("_end_mono", None); august.pop("_start_mono", None); august.pop("_end_mono", None)
                    record = {"group": group, "group_elapsed_ms": round((time.monotonic() - group_start) * 1000, 1), "july": july, "august": august, "overlap_ms": round(overlap * 1000, 1), "pending_after_group": pending_after_group}
                    f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n"); f.flush()
                    print(json.dumps({"group": group, "status": july["status"] + "/" + august["status"], "group_elapsed_ms": record["group_elapsed_ms"], "july_ms": july["elapsed_ms"], "august_ms": august["elapsed_ms"]}), flush=True)
    finally:
        for m in (meta_client_manager, dw_client_manager, es_client_manager, qdrant_client_manager):
            try: await m.close()
            except Exception: pass

if __name__ == "__main__": asyncio.run(main())
