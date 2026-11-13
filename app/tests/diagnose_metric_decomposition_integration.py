"""真实 MySQL/检索/LLM MetricDecompositionAgent 验收；不接入 RootCause。"""

from __future__ import annotations

import asyncio
import json
import time
import traceback
from types import SimpleNamespace
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text

from app.agent.anomaly_agent.MetricDecomposition_Agent.graph import metric_decomposition_graph
from app.agent.anomaly_agent.MetricDecomposition_Agent.schemas import MetricDecompositionInput
from app.agent.anomaly_agent.Main_RootCause_Agent.skills.SalesAmountAnomalySkill.sales_amount_anomaly import SALES_AMOUNT_ANOMALY_SKILL
from app.agent.anomaly_agent.common.change_inspection.tool import verify_metric_change
from app.agent.anomaly_agent.common.change_inspection.nodes.load_metric_context import _build_metric_context
from app.agent.anomaly_agent.schemas import AnalysisRequest, InspectionOutcome, FilterCondition
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


async def main() -> None:
    started = time.monotonic()
    report = {"status": "failed", "nodes": [], "pending_tasks": []}
    for manager in (meta_client_manager, dw_client_manager, es_client_manager, qdrant_client_manager, embedding_client_manager):
        manager.init()
    try:
        async with meta_client_manager.session_factory() as meta_session, dw_client_manager.session_factory() as dw_session:
            meta_repo = MetaMySQLRepository(meta_session)
            dw_repo = DWMySQLRepository(dw_session)
            record = await meta_repo.get_metric_by_name("销售额")
            if record is None:
                raise RuntimeError("Meta 中不存在已发布销售额指标")
            metric = _build_metric_context(record)
            request = AnalysisRequest(
                metric="销售额", request_id="metric-decomposition-real", target_period="2026-08",
                comparison_period="2026-07", comparison_type="mom",
                filters=[FilterCondition(dimension="城市", value="北京")], expected_direction="decrease",
            )
            context: DataAgentContext = {
                "metric_qdrant_repository": MetricQdrantRepository(qdrant_client_manager.client),
                "column_qdrant_repository": ColumnQdrantRepository(qdrant_client_manager.client),
                "value_es_repository": ValueESRepository(es_client_manager.client),
                "embedding_client": embedding_client_manager.client,
                "meta_mysql_repository": meta_repo, "dw_mysql_repository": dw_repo,
                "meta_session_factory": meta_client_manager.session_factory,
                "dw_session_factory": dw_client_manager.session_factory,
                "llm_provider": DEFAULT_LLM_PROVIDER, "llm_model_tier": DEFAULT_LLM_MODEL,
            }
            inspection = await verify_metric_change(
                request, SimpleNamespace(context=context, stream_writer=lambda _event: None),
            )
            if inspection.status != "ready":
                raise RuntimeError(f"ChangeInspection 未 ready：{inspection.status}/{inspection.reason}")
            overall = inspection.overall_result
            input_data = MetricDecompositionInput(
                raw_question="为什么 2026 年 8 月北京销售额比 7 月下降？销售额由哪些因素构成？",
                request=request, inspection_outcome=inspection, metric_context=metric,
                skill_name=SALES_AMOUNT_ANOMALY_SKILL.name, skill_version=SALES_AMOUNT_ANOMALY_SKILL.version,
                skill_strategy=SALES_AMOUNT_ANOMALY_SKILL.strategy,
            )
            state = {}
            stream = metric_decomposition_graph.astream(
                {"input": input_data}, context=context, stream_mode=["updates", "values"],
            )
            try:
                async for mode, chunk in stream:
                    if mode == "updates" and isinstance(chunk, dict):
                        report["nodes"].extend(str(name) for name in chunk)
                    elif mode == "values" and isinstance(chunk, dict):
                        state.update(chunk)
            finally:
                await stream.aclose()
            result = state.get("result")
            report.update({
                "status": "passed" if result and result.status == "completed" and result.reconciliation and result.reconciliation.matches else "failed",
                "overall": overall.model_dump(mode="json"),
                "result": result.model_dump(mode="json") if result else None,
            })
    except Exception as exc:
        report.update({"exception_type": type(exc).__name__, "error": str(exc), "traceback": traceback.format_exc()})
    finally:
        report["elapsed_ms"] = round((time.monotonic() - started) * 1000, 1)
        report["pending_tasks"] = [task.get_name() for task in asyncio.all_tasks() if task is not asyncio.current_task() and not task.done()]
        for manager in (meta_client_manager, dw_client_manager, es_client_manager, qdrant_client_manager):
            try:
                await manager.close()
            except Exception:
                pass
        path = ROOT / "diagnostics" / "metric_decomposition_real.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
        print(json.dumps({k: report.get(k) for k in ("status", "elapsed_ms", "nodes", "pending_tasks", "exception_type", "error")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
