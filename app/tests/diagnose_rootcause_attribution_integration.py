"""真实 RootCause → ChangeInspection → DimensionAttributionAgent 验收。"""

from __future__ import annotations

import argparse
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


QUESTIONS = {
    "A": "为什么 2026 年 8 月北京销售额相比 7 月下降？哪些渠道影响最大？",
    "B": "为什么 2026 年 8 月北京销售额比 7 月下降？",
    "C": "为什么 2026 年 8 月北京销售额比 7 月上涨？",
}
ROOT = Path(__file__).resolve().parents[2]


def merge_state(state: dict, chunk: object) -> None:
    if isinstance(chunk, dict):
        state.update(chunk)


async def main(scenario: str) -> None:
    question = QUESTIONS[scenario]
    out = ROOT / "diagnostics" / f"rootcause_attribution_{scenario.lower()}.json"
    started = time.monotonic()
    report: dict = {"scenario": scenario, "question": question, "status": "failed"}
    state: dict = {}
    nodes: list[str] = []
    for manager in (meta_client_manager, dw_client_manager, es_client_manager, qdrant_client_manager, embedding_client_manager):
        manager.init()
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
                stream = main_root_cause_agent.astream(
                    {"raw_question": question}, context=context,
                    stream_mode=["updates", "values"],
                )
                try:
                    async for mode, chunk in stream:
                        if mode == "updates" and isinstance(chunk, dict):
                            nodes.extend(str(name) for name in chunk)
                        if mode == "values":
                            merge_state(state, chunk)
                finally:
                    await stream.aclose()

            await asyncio.wait_for(consume(), timeout=900)

        observations = state.get("observations") or []
        inspection = next((x for x in observations if x.get("action_name") == "change_inspection"), None)
        attribution = next((x for x in observations if x.get("action_name") == "dimension_attribution"), None)
        result = (attribution or {}).get("attribution_result") or {}
        overall = (inspection or {}).get("evidence") or {}
        values_ok = all(
            Decimal(str(overall.get(key))) == value
            for key, value in {
                "comparison_value": Decimal("233378.95"),
                "current_value": Decimal("120953.99"),
                "change_amount": Decimal("-112424.96"),
            }.items()
        ) if inspection and inspection.get("status") == "ready" else False
        if scenario == "C":
            passed = bool(inspection and inspection.get("status") == "rejected"
                          and inspection.get("reason") == "direction_mismatch" and not attribution)
        else:
            paths = result.get("paths") or []
            path_ok = all(all(path["reconciliation"][key] for key in
                              ("comparison_matches", "current_matches", "change_matches")) for path in paths)
            channel_ok = any(path.get("dimension_column") == "dim_store.channel_type" for path in paths)
            passed = bool(values_ok and attribution and result.get("status") == "completed"
                          and paths and path_ok and (scenario != "A" or channel_ok))
        report.update(
            status="passed" if passed else "failed", nodes=nodes, state=state,
            observations=observations, inspection=inspection, attribution=attribution,
            values_ok=values_ok, pending_tasks=[
                task.get_name() for task in asyncio.all_tasks()
                if task is not asyncio.current_task() and not task.done()
            ],
        )
    except Exception as exc:
        report.update(exception_type=type(exc).__name__, error=str(exc), traceback=traceback.format_exc(), nodes=nodes, state=state)
    finally:
        report["elapsed_ms"] = round((time.monotonic() - started) * 1000, 1)
        for manager in (meta_client_manager, dw_client_manager, es_client_manager, qdrant_client_manager):
            try:
                await manager.close()
            except Exception:
                pass
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
        print(json.dumps({key: report.get(key) for key in
                          ("scenario", "status", "elapsed_ms", "nodes", "values_ok", "pending_tasks", "exception_type", "error")},
                         ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=QUESTIONS)
    asyncio.run(main(parser.parse_args().scenario))
