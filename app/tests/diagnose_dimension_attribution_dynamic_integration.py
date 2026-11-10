"""真实服务验收 DimensionAttributionAgent 的 LLM 动态维度与下钻。"""
from __future__ import annotations

import asyncio
import json
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

from app.agent.anomaly_agent.DimensionAttribution_Agent.graph import dimension_attribution_graph
from app.agent.anomaly_agent.DimensionAttribution_Agent.schemas import DimensionAttributionInput
from app.agent.anomaly_agent.DimensionAttribution_Agent.tools import ATTRIBUTION_TOOL_REGISTRY
from app.agent.anomaly_agent.Main_RootCause_Agent.skills.SalesAmountAnomalySkill.sales_amount_anomaly import SALES_AMOUNT_ANOMALY_SKILL
from app.agent.anomaly_agent.common.change_inspection.tool import verify_metric_change
from app.agent.anomaly_agent.schemas import AnalysisRequest, FilterCondition
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


QUESTION = "为什么 2026 年 8 月北京销售额比 7 月下降？"
OUT = Path(__file__).resolve().parents[2] / "diagnostics" / "dimension_attribution_dynamic_integration.json"


def merge_state(state: dict, chunk: object) -> None:
    if not isinstance(chunk, dict):
        return
    for key, value in chunk.items():
        state[key] = value


async def main() -> None:
    for manager in (meta_client_manager, dw_client_manager, es_client_manager, qdrant_client_manager, embedding_client_manager):
        manager.init()
    started = time.monotonic()
    report: dict = {"question": QUESTION, "status": "failed"}
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
            runtime = SimpleNamespace(context=context, stream_writer=lambda event: None)
            request = AnalysisRequest(
                metric="销售额", request_id="dimension-attribution-dynamic-2026-08-beijing",
                target_period="2026-08", comparison_period="2026-07", comparison_type="mom",
                filters=[FilterCondition(dimension="城市", value="北京")],
                expected_direction="decrease",
            )
            inspection = await verify_metric_change(request, runtime)
            report["inspection"] = inspection.model_dump(mode="json")
            if inspection.status != "ready":
                report["failure"] = "ChangeInspection 未返回 ready"
            else:
                input_data = DimensionAttributionInput(
                    raw_question=QUESTION, request=request, inspection_outcome=inspection,
                    metric_context=inspection.metric_context,
                    skill_name=SALES_AMOUNT_ANOMALY_SKILL.name,
                    skill_version=SALES_AMOUNT_ANOMALY_SKILL.version,
                    skill_strategy=SALES_AMOUNT_ANOMALY_SKILL.strategy,
                    available_tool_names=tuple(ATTRIBUTION_TOOL_REGISTRY),
                )
                state: dict = {}
                nodes: list[str] = []
                async for mode, chunk in dimension_attribution_graph.astream(
                    {"input": input_data}, context=context, stream_mode=["updates", "values"]
                ):
                    if mode == "updates" and isinstance(chunk, dict):
                        nodes.extend(str(name) for name in chunk)
                        merge_state(state, next(iter(chunk.values())))
                    elif mode == "values":
                        merge_state(state, chunk)
                result = state.get("result")
                report.update(
                    status="passed" if result and result.status == "completed" else "failed",
                    nodes=nodes,
                    state={key: (value.model_dump(mode="json") if hasattr(value, "model_dump") else value) for key, value in state.items() if key != "input"},
                    result=result.model_dump(mode="json") if result else None,
                    pending_tasks=[t.get_name() for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()],
                )
    except Exception as exc:
        report.update(exception_type=type(exc).__name__, error=str(exc), traceback=traceback.format_exc())
    finally:
        report["elapsed_ms"] = round((time.monotonic() - started) * 1000, 1)
        for manager in (meta_client_manager, dw_client_manager, es_client_manager, qdrant_client_manager):
            try:
                await manager.close()
            except Exception:
                pass
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(report, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
        print(json.dumps({"status": report.get("status"), "elapsed_ms": report.get("elapsed_ms"), "nodes": report.get("nodes"), "pending_tasks": report.get("pending_tasks")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
