"""BusinessDiagnosisAgent 第二阶段真实验收。

在 Mac 正常终端执行；会复用同一请求的真实 RootCause 证据，并允许
scoped_metric_decomposition 通过真实 DataQueryTool 执行 4 条 SQL。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import traceback
from pathlib import Path

from app.agent.anomaly_agent.BusinessDiagnosis_Agent.graph import business_diagnosis_graph
from app.agent.anomaly_agent.BusinessDiagnosis_Agent.schemas import BusinessDiagnosisInput
from app.agent.anomaly_agent.Main_RootCause_Agent.skills.registry import load_skill
from app.agent.data_agent.context import DataAgentContext
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
SOURCE = ROOT / "diagnostics" / "rootcause_decomposition_c.json"
OUTPUT = ROOT / "diagnostics" / "business_diagnosis_scoped_real.json"


def load_input(path: Path) -> BusinessDiagnosisInput:
    from app.tests.diagnose_business_diagnosis_real import SOURCE as OLD_SOURCE, _load_input
    import app.tests.diagnose_business_diagnosis_real as source_module
    source_module.SOURCE = path
    input_data, _ = _load_input()
    source_module.SOURCE = OLD_SOURCE
    input_data.available_tool_names = ("scoped_metric_decomposition",)
    return input_data


async def run(source: Path) -> dict:
    started = time.monotonic()
    report: dict = {"status": "failed", "source": str(source), "sql_query_count": 0, "pending_tasks": []}
    managers = (meta_client_manager, dw_client_manager, es_client_manager, qdrant_client_manager, embedding_client_manager)
    for manager in managers:
        manager.init()
    try:
        input_data = load_input(source)
        async with meta_client_manager.session_factory() as meta_session, dw_client_manager.session_factory() as dw_session:
            context: DataAgentContext = {
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
            state: dict = {}
            nodes: list[str] = []
            stream = business_diagnosis_graph.astream({"input": input_data}, context=context, stream_mode=["updates", "values"])
            try:
                async for mode, chunk in stream:
                    if mode == "updates" and isinstance(chunk, dict):
                        nodes.extend(str(name) for name in chunk)
                    elif mode == "values" and isinstance(chunk, dict):
                        state.update(chunk)
            finally:
                await stream.aclose()
        result = state.get("result")
        scoped_evidence = [item for item in state.get("evidence_items", []) if item.kind == "scoped_metric_decomposition" and item.reconciled]
        scoped_observations = [item for item in state.get("observations", []) if item.action_name == "scoped_metric_decomposition" and item.status == "ok"]
        node_counts = {name: nodes.count(name) for name in ("prepare_evidence", "main_reason", "execute_diagnosis_tools", "finalize_diagnosis")}
        acceptance = bool(
            result
            and result.status == "completed"
            and result.completion_status == "complete"
            and state.get("query_count") == 4
            and len(scoped_evidence) == 1
            and len(scoped_observations) == 1
            and node_counts["execute_diagnosis_tools"] >= 1
            and node_counts["main_reason"] >= 2
            and not [task for task in asyncio.all_tasks() if task is not asyncio.current_task() and not task.done()]
        )
        report.update({
            "status": "passed" if acceptance else "failed",
            "acceptance": acceptance,
            "nodes": nodes,
            "node_counts": node_counts,
            "result": result.model_dump(mode="json") if result else None,
            "observations": [item.model_dump(mode="json") for item in state.get("observations", [])],
            "query_count": state.get("query_count", 0),
            "round_count": state.get("round_count", 0),
            "evidence": [item.model_dump(mode="json") for item in state.get("evidence_items", [])],
        })
    except Exception as exc:
        report.update(exception_type=type(exc).__name__, error=str(exc), traceback=traceback.format_exc())
    finally:
        report["pending_tasks"] = [task.get_name() for task in asyncio.all_tasks() if task is not asyncio.current_task() and not task.done()]
        report["sql_query_count"] = report.get("query_count", 0)
        report["elapsed_ms"] = round((time.monotonic() - started) * 1000, 1)
        for manager in managers:
            try:
                await manager.close()
            except Exception:
                pass
        OUTPUT.write_text(json.dumps(report, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=SOURCE)
    args = parser.parse_args()
    source = args.source if args.source.is_absolute() else ROOT / args.source
    print(json.dumps(asyncio.run(run(source)), ensure_ascii=False, default=str, indent=2), flush=True)
