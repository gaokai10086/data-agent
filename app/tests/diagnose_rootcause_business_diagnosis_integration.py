"""RootCause -> 专业 Agent -> BusinessDiagnosisAgent 真实集成验收。

本脚本只在可访问 MySQL、Qdrant、Elasticsearch、Embedding 和 DeepSeek 的
正常终端执行；当前 Codex 沙箱内不要将网络失败报告为业务失败。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import traceback
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
    "A": "为什么 2026 年 8 月北京销售额相比 7 月下降？请分析渠道贡献、订单量与客单价，并进一步诊断主要影响渠道的下降因素。",
    "B": "为什么 2026 年 8 月北京销售额相比 7 月下降？请进一步诊断主要影响因素。",
    "C": "为什么 2026 年 8 月北京销售额相比 7 月上涨？请进一步诊断原因。",
}


async def run(scenario: str) -> dict:
    report: dict = {"scenario": scenario, "question": QUESTIONS[scenario], "status": "failed"}
    state: dict = {}
    nodes: list[str] = []
    started = time.monotonic()
    managers = (meta_client_manager, dw_client_manager, es_client_manager, qdrant_client_manager, embedding_client_manager)
    for manager in managers:
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
                    {"raw_question": QUESTIONS[scenario]}, context=context,
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

            await asyncio.wait_for(consume(), timeout=1800)

        observations = state.get("observations") or []
        inspection = next((item for item in observations if item.get("action_name") == "change_inspection"), None)
        business = next((item for item in observations if item.get("action_name") == "business_diagnosis"), None)
        if scenario == "C":
            acceptance = bool(
                inspection and inspection.get("status") == "rejected"
                and inspection.get("reason") == "direction_mismatch"
                and business is None
            )
        else:
            result = (business or {}).get("business_diagnosis_result") or {}
            acceptance = bool(
                inspection and inspection.get("status") == "ready"
                and business and result.get("status") in {"completed", "partial"}
                and result.get("evidence_ids")
            )
        report.update(
            status="passed" if acceptance else "failed",
            acceptance=acceptance,
            nodes=nodes,
            observations=observations,
            inspection=inspection,
            business_diagnosis=business,
            final_stop_reason=state.get("stop_reason"),
            pending_tasks=[
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
        out = ROOT / "diagnostics" / f"rootcause_business_diagnosis_{scenario.lower()}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=QUESTIONS)
    result = asyncio.run(run(parser.parse_args().scenario))
    print(json.dumps({key: result.get(key) for key in (
        "scenario", "status", "acceptance", "elapsed_ms", "nodes", "pending_tasks", "exception_type", "error",
    )}, ensure_ascii=False), flush=True)
