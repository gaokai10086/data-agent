"""验证第四个节点：根据指标定义查询两个比较周期的总体值。"""

from __future__ import annotations

import asyncio
import json

from app.agent.orchestrator.graph import orchestrator_graph
from app.clients.embedding_client import embedding_client_manager
from app.clients.es_client import es_client_manager
from app.clients.mysql_client import dw_client_manager, meta_client_manager
from app.clients.qdrant_client import qdrant_client_manager
from app.repositories.es.value_es_repository import ValueESRepository
from app.repositories.mysql.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository
from app.repositories.qdrant.column_repository_qdrant import ColumnQdrantRepository
from app.repositories.qdrant.metric_repository_qdrant import MetricQdrantRepository


# 只修改这个问题，即可验证不同的总体异动查询。
QUESTION = "为什么2025年7月销售额比2025年6月下降了？"


def print_json(title: str, value: object) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    print(title)
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


async def main() -> None:
    meta_client_manager.init()
    dw_client_manager.init()
    qdrant_client_manager.init()
    embedding_client_manager.init()
    es_client_manager.init()
    try:
        async with (
            meta_client_manager.get_session() as meta_session,
            dw_client_manager.get_session() as dw_session,
        ):
            result = await orchestrator_graph.ainvoke(
                {"query": QUESTION},
                context={
                    "meta_mysql_repository": MetaMySQLRepository(meta_session),
                    "dw_mysql_repository": DWMySQLRepository(dw_session),
                    "metric_qdrant_repository": MetricQdrantRepository(qdrant_client_manager.client),
                    "column_qdrant_repository": ColumnQdrantRepository(qdrant_client_manager.client),
                    "value_es_repository": ValueESRepository(es_client_manager.client),
                    "embedding_client": embedding_client_manager.client,
                },
            )
    finally:
        await meta_client_manager.close()
        await dw_client_manager.close()
        await qdrant_client_manager.close()
        await es_client_manager.close()

    print("===== Node 1：Intent Detection =====")
    print_json("输入：", {"query": QUESTION})
    print_json("输出：", {
        "intent": result.get("intent"),
        "intent_reason": result.get("intent_reason", ""),
    })

    if result.get("intent") != "anomaly_analysis":
        print("本次没有分流到异动归因 Agent，后续节点不执行。")
        return

    anomaly_result = result["agent_result"]
    request = anomaly_result["analysis_request"]
    metric_context = anomaly_result["metric_context"]

    print("===== Node 2：Parse Request =====")
    print_json("输入：", {"raw_question": QUESTION})
    print_json("输出：", request)

    print("===== Node 3：Load Metric Context =====")
    print_json("输入：", {"analysis_request": request})
    print_json("输出：", metric_context)

    print("===== Node 4：Query Overall =====")
    print_json("输入：", {
        "analysis_request": request,
        "metric_context": metric_context,
    })
    print_json("输出：", anomaly_result["overall_result"])


if __name__ == "__main__":
    asyncio.run(main())
