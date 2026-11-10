"""真实 DataQueryTool 连续调用稳定性诊断。

只读使用真实 MySQL、Qdrant、Elasticsearch、Embedding 和 LLM；每轮结果
立即追加到 JSONL，便于长时间测试被外部终止时保留已完成证据。
"""

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


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "diagnostics" / "data_query_repeat_10.jsonl"
QUERY = "查询 2026 年 8 月北京地区销售额，不按维度分组，只返回 value。"
EXPECTED = Decimal("120953.99")
ROUND_TIMEOUT = 90.0


def _event_collector(events: list[dict], trace_id: str):
    def write(event):
        if isinstance(event, dict):
            events.append({"at": datetime.now().isoformat(), "trace_id": trace_id, **event})
    return write


async def _run_round(tool: DataQueryTool, context: dict, round_no: int) -> dict:
    trace_id = f"dq-repeat-{round_no:02d}-{uuid.uuid4().hex[:8]}"
    events: list[dict] = []
    request = DataQueryRequest(
        query=QUERY,
        request_id=trace_id,
        purpose="repeat_stability_diagnostic",
        role="目标",
        period="2026-08",
        metric_key="sales_amount",
        expected_columns=["value"],
        max_rows=1,
    )
    runtime = SimpleNamespace(context=dict(context), stream_writer=_event_collector(events, trace_id))
    started = time.monotonic()
    result: dict = {
        "round": round_no,
        "trace_id": trace_id,
        "started_at": datetime.now().isoformat(),
        "query_length": len(QUERY),
        "model": DEFAULT_LLM_MODEL,
        "provider": DEFAULT_LLM_PROVIDER,
        "events": events,
    }
    try:
        data_result = await asyncio.wait_for(tool.run(request, runtime), timeout=ROUND_TIMEOUT)
        value = data_result.rows[0].get("value") if data_result.rows else None
        result.update(
            status="success" if data_result.success and Decimal(str(value)) == EXPECTED else "business_failure",
            success=bool(data_result.success),
            value=str(value) if value is not None else None,
            sql=data_result.sql,
            error_code=data_result.error_code,
            error_message=data_result.error_message,
        )
    except asyncio.TimeoutError as exc:
        result.update(status="round_timeout", exception_type=type(exc).__name__, traceback=traceback.format_exc())
    except asyncio.CancelledError:
        result.update(status="cancelled", exception_type="CancelledError", traceback=traceback.format_exc())
        raise
    except Exception as exc:
        result.update(status="exception", exception_type=type(exc).__name__, error=str(exc), traceback=traceback.format_exc())
    finally:
        result["ended_at"] = datetime.now().isoformat()
        result["elapsed_ms"] = round((time.monotonic() - started) * 1000, 1)
        result["unfinished_tasks"] = [
            repr(task)
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task() and not task.done()
        ]
    return result


async def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    for manager in (meta_client_manager, dw_client_manager, es_client_manager, qdrant_client_manager, embedding_client_manager):
        manager.init()
    context = None
    try:
        async with meta_client_manager.session_factory() as meta_session, dw_client_manager.session_factory() as dw_session:
            context = {
                "metric_qdrant_repository": MetricQdrantRepository(qdrant_client_manager.client),
                "column_qdrant_repository": ColumnQdrantRepository(qdrant_client_manager.client),
                "value_es_repository": ValueESRepository(es_client_manager.client),
                "embedding_client": embedding_client_manager.client,
                "meta_mysql_repository": MetaMySQLRepository(meta_session),
                "dw_mysql_repository": DWMySQLRepository(dw_session),
                "meta_session_factory": meta_client_manager.session_factory,
                "dw_session_factory": dw_client_manager.session_factory,
                "llm_provider": DEFAULT_LLM_PROVIDER,
                "llm_model_tier": DEFAULT_LLM_MODEL,
            }
            tool = DataQueryTool()
            with OUTPUT.open("w", encoding="utf-8") as output:
                for round_no in range(1, 11):
                    record = await _run_round(tool, context, round_no)
                    output.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
                    output.flush()
                    print(json.dumps({k: record.get(k) for k in ("round", "status", "elapsed_ms", "value", "exception_type")}, ensure_ascii=False), flush=True)
    finally:
        for manager in (meta_client_manager, dw_client_manager, es_client_manager, qdrant_client_manager):
            try:
                await manager.close()
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main())
