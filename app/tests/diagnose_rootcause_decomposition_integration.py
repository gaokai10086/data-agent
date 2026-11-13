"""RootCause -> ChangeInspection -> Metric/Dimension 专业 Agent 真实验收。"""
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

ROOT = Path(__file__).resolve().parents[2]
QUESTIONS = {
    "A": "为什么 2026 年 8 月北京销售额相比 7 月下降？是订单量还是客单价造成的？",
    "B": "为什么 2026 年 8 月北京销售额相比 7 月下降？哪些渠道影响最大？",
    "C": "为什么 2026 年 8 月北京销售额相比 7 月下降？分析渠道贡献以及订单量和客单价因素。",
    "D": "为什么 2026 年 8 月北京销售额相比 7 月上涨？",
}


async def run(scenario: str) -> dict:
    question = QUESTIONS[scenario]
    report: dict = {"scenario": scenario, "question": question, "status": "failed"}
    state: dict = {}
    nodes: list[str] = []
    started = time.monotonic()
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
                        elif mode == "values" and isinstance(chunk, dict):
                            state.update(chunk)
                finally:
                    await stream.aclose()

            await asyncio.wait_for(consume(), timeout=1200)
        observations = state.get("observations") or []
        inspection = next((x for x in observations if x.get("action_name") == "change_inspection"), None)
        decomposition = next((x for x in observations if x.get("action_name") == "metric_decomposition"), None)
        attribution = next((x for x in observations if x.get("action_name") == "dimension_attribution"), None)
        report.update(
            status="completed",
            nodes=nodes,
            observations=observations,
            inspection=inspection,
            decomposition=decomposition,
            attribution=attribution,
            final_stop_reason=state.get("stop_reason"),
            pending_tasks=[t.get_name() for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()],
        )
        if scenario == "D":
            report["acceptance"] = bool(inspection and inspection.get("status") == "rejected" and inspection.get("reason") == "direction_mismatch" and not decomposition and not attribution)
        elif scenario == "A":
            result = (decomposition or {}).get("decomposition_result") or {}
            report["acceptance"] = bool(inspection and inspection.get("status") == "ready" and decomposition and result.get("status") == "completed" and result.get("completion_status") == "complete")
        elif scenario == "B":
            result = (attribution or {}).get("attribution_result") or {}
            report["acceptance"] = bool(inspection and inspection.get("status") == "ready" and attribution and result.get("status") == "completed")
        else:
            report["acceptance"] = bool(inspection and inspection.get("status") == "ready" and decomposition and attribution)
        report["status"] = "passed" if report["acceptance"] else "failed"
    except Exception as exc:
        report.update(exception_type=type(exc).__name__, error=str(exc), traceback=traceback.format_exc(), nodes=nodes, state=state)
    finally:
        report["elapsed_ms"] = round((time.monotonic() - started) * 1000, 1)
        for manager in (meta_client_manager, dw_client_manager, es_client_manager, qdrant_client_manager):
            try:
                await manager.close()
            except Exception:
                pass
    out = ROOT / "diagnostics" / f"rootcause_decomposition_{scenario.lower()}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=QUESTIONS)
    result = asyncio.run(run(parser.parse_args().scenario))
    print(json.dumps({k: result.get(k) for k in ("scenario", "status", "acceptance", "elapsed_ms", "nodes", "exception_type", "error", "pending_tasks")}, ensure_ascii=False), flush=True)
