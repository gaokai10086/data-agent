"""真实 DataQueryTool 串行双周期稳定性测试。"""
from __future__ import annotations

import asyncio
import json
import time
import traceback
import uuid
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

OUT = Path(__file__).resolve().parents[2] / "diagnostics" / "data_query_serial_pairs_5.jsonl"
EXPECTED = {"2026-07": Decimal("233378.95"), "2026-08": Decimal("120953.99")}
TIMEOUT = 90.0


async def run_query(tool, context, period: str, group: int) -> dict:
    trace = f"serial-pair-{group}-{period}-{uuid.uuid4().hex[:8]}"
    events: list[dict] = []
    runtime = SimpleNamespace(context=dict(context), stream_writer=lambda event: events.append({"at": datetime.now().isoformat(), **(event if isinstance(event, dict) else {"event": str(event)})}))
    request = DataQueryRequest(
        query=f"查询销售额在 {period} 的总体值，不按维度分组，筛选城市=北京，只返回一行，列名为 value。",
        request_id=trace,
        purpose="serial_pair_diagnostic",
        role="目标" if period == "2026-08" else "对比",
        period=period,
        metric_key="sales_amount",
        expected_columns=["value"],
        max_rows=1,
    )
    started = time.monotonic()
    record = {"group": group, "period": period, "trace_id": trace, "started_at": datetime.now().isoformat(), "events": events}
    try:
        result = await asyncio.wait_for(tool.run(request, runtime), timeout=TIMEOUT)
        value = result.rows[0].get("value") if result.rows else None
        record.update(status="success" if result.success and Decimal(str(value)) == EXPECTED[period] else "business_failure", value=str(value) if value is not None else None, sql=result.sql, success=result.success, error=result.error_message)
    except asyncio.CancelledError:
        record.update(status="cancelled", exception_type="CancelledError", traceback=traceback.format_exc())
        raise
    except asyncio.TimeoutError:
        record.update(status="timeout", exception_type="TimeoutError", traceback=traceback.format_exc())
    except Exception as exc:
        record.update(status="exception", exception_type=type(exc).__name__, error=str(exc), traceback=traceback.format_exc())
    finally:
        record.update(ended_at=datetime.now().isoformat(), elapsed_ms=round((time.monotonic() - started) * 1000, 1), unfinished_tasks=sum(1 for task in asyncio.all_tasks() if task is not asyncio.current_task() and not task.done()))
    return record


async def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    for manager in (meta_client_manager, dw_client_manager, es_client_manager, qdrant_client_manager, embedding_client_manager): manager.init()
    try:
        async with meta_client_manager.session_factory() as meta_session, dw_client_manager.session_factory() as dw_session:
            context = {"metric_qdrant_repository": MetricQdrantRepository(qdrant_client_manager.client), "column_qdrant_repository": ColumnQdrantRepository(qdrant_client_manager.client), "value_es_repository": ValueESRepository(es_client_manager.client), "embedding_client": embedding_client_manager.client, "meta_mysql_repository": MetaMySQLRepository(meta_session), "dw_mysql_repository": DWMySQLRepository(dw_session), "meta_session_factory": meta_client_manager.session_factory, "dw_session_factory": dw_client_manager.session_factory, "llm_provider": DEFAULT_LLM_PROVIDER, "llm_model_tier": DEFAULT_LLM_MODEL}
            tool = DataQueryTool()
            with OUT.open("w", encoding="utf-8") as output:
                for group in range(1, 6):
                    a = await run_query(tool, context, "2026-07", group)
                    output.write(json.dumps(a, ensure_ascii=False, default=str) + "\n"); output.flush(); print(json.dumps({k: a.get(k) for k in ("group", "period", "status", "elapsed_ms", "value")}, ensure_ascii=False), flush=True)
                    b = await run_query(tool, context, "2026-08", group)
                    output.write(json.dumps(b, ensure_ascii=False, default=str) + "\n"); output.flush(); print(json.dumps({k: b.get(k) for k in ("group", "period", "status", "elapsed_ms", "value")}, ensure_ascii=False), flush=True)
    finally:
        for manager in (meta_client_manager, dw_client_manager, es_client_manager, qdrant_client_manager):
            try: await manager.close()
            except Exception: pass


if __name__ == "__main__": asyncio.run(main())
