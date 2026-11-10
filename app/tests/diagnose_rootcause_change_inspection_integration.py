"""真实 RootCause Supervisor -> ChangeInspection 集成验收。"""
from __future__ import annotations

import asyncio
import json
import time
import traceback
from decimal import Decimal
from pathlib import Path

from app.agent.anomaly_agent.Main_RootCause_Agent.graph import main_root_cause_agent
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

OUT = Path(__file__).resolve().parents[2] / "diagnostics" / "rootcause_change_inspection_integration.json"
QUESTION = "为什么 2026 年 8 月北京地区销售额相比 7 月下降？"
EXPECTED = {
    "current_value": Decimal("120953.99"),
    "comparison_value": Decimal("233378.95"),
    "change_amount": Decimal("-112424.96"),
}
TIMEOUT = 240.0


def _merge_state(state: dict, chunk: object) -> None:
    if isinstance(chunk, dict):
        for key, value in chunk.items():
            if isinstance(value, dict) and isinstance(state.get(key), dict):
                state[key] = {**state[key], **value}
            else:
                state[key] = value


async def main() -> None:
    for manager in (meta_client_manager, dw_client_manager, es_client_manager, qdrant_client_manager, embedding_client_manager):
        manager.init()

    started = time.monotonic()
    state: dict = {}
    nodes: list[str] = []
    custom_events: list[object] = []
    result: dict = {"question": QUESTION, "status": "failed"}
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
            async def consume() -> None:
                async for event in main_root_cause_agent.astream(
                    {"raw_question": QUESTION},
                    context=context,
                    stream_mode=["updates", "values", "custom"],
                ):
                    if not isinstance(event, tuple) or len(event) != 2:
                        continue
                    mode, chunk = event
                    if mode == "updates" and isinstance(chunk, dict):
                        for node, update in chunk.items():
                            nodes.append(str(node))
                            _merge_state(state, update)
                    elif mode == "values":
                        _merge_state(state, chunk)
                    elif mode == "custom":
                        custom_events.append(chunk)

            await asyncio.wait_for(consume(), timeout=TIMEOUT)

        observations = state.get("observations") or []
        ready = next((item for item in observations if item.get("status") == "ready"), None)
        evidence = (ready or {}).get("evidence") or {}
        values_ok = False
        if ready and all(evidence.get(key) is not None for key in ("current_value", "comparison_value", "change_amount")):
            values_ok = (
                Decimal(str(evidence["current_value"])) == EXPECTED["current_value"]
                and Decimal(str(evidence["comparison_value"])) == EXPECTED["comparison_value"]
                and Decimal(str(evidence["change_amount"])) == EXPECTED["change_amount"]
            )
        completed_rejection = bool(observations and observations[-1].get("status") == "rejected")
        result.update(
            status="passed" if ready and values_ok else ("completed_rejected" if completed_rejection else "failed"),
            elapsed_ms=round((time.monotonic() - started) * 1000, 1),
            nodes=nodes,
            observations=observations,
            supervisor_decisions=state.get("supervisor_decision"),
            evidence=evidence,
            final_state=state,
            custom_event_count=len(custom_events),
            values_ok=values_ok,
            pending_tasks=[t.get_name() for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()],
        )
    except asyncio.CancelledError:
        result.update(status="cancelled", exception_type="CancelledError", traceback=traceback.format_exc())
        raise
    except Exception as exc:
        result.update(status="failed", elapsed_ms=round((time.monotonic() - started) * 1000, 1), exception_type=type(exc).__name__, error=str(exc), traceback=traceback.format_exc(), nodes=nodes, partial_state=state)
    finally:
        for manager in (meta_client_manager, dw_client_manager, es_client_manager, qdrant_client_manager):
            try:
                await manager.close()
            except Exception:
                pass
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(result, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
        print(json.dumps({"status": result.get("status"), "elapsed_ms": result.get("elapsed_ms"), "nodes": result.get("nodes"), "values_ok": result.get("values_ok"), "pending_tasks": result.get("pending_tasks")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
