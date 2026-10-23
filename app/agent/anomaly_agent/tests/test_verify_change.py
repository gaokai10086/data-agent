"""真实链路验证：意图识别 → 请求解析 → 指标上下文 → 总体查询 → 变化核实。

在 PyCharm 运行本文件，或在项目根目录执行 python -m app.agent.anomaly_agent.tests.test_verify_change。
只修改下面的 QUESTION；指标必须在 Metadata 中已发布，目前支持两个明确月份的总体对比。
本脚本会调用模型与数据库。
"""
from __future__ import annotations


# 用户问题统一放在入口顶部；其余步骤从 Graph 结果读取指标与周期。
QUESTION = "为什么2025年7月上海销售额比2025年6月下降了？"


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


def _json_default(value: object):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return str(value)


def print_json(title: str, value: object) -> None:
    """嵌套模型也输出 JSON 对象，避免显示成 metric='...' 的字符串。"""
    print(title, flush=True)
    print(json.dumps(value, ensure_ascii=False, indent=2, default=_json_default), flush=True)


async def run_question(question: str) -> dict:
    """与同目录总体查询脚本使用同一 Orchestrator 和资源注入方式。"""
    meta_client_manager.init()
    dw_client_manager.init()
    qdrant_client_manager.init()
    embedding_client_manager.init()
    es_client_manager.init()
    try:
        async with meta_client_manager.get_session() as meta_session, dw_client_manager.get_session() as dw_session:
            return await orchestrator_graph.ainvoke(
                {"query": question},
                context={
                    "meta_mysql_repository": MetaMySQLRepository(meta_session),
                    "dw_mysql_repository": DWMySQLRepository(dw_session),
                    "dw_session_factory": dw_client_manager.session_factory,
                    "column_qdrant_repository": ColumnQdrantRepository(qdrant_client_manager.client),
                    "metric_qdrant_repository": MetricQdrantRepository(qdrant_client_manager.client),
                    "value_es_repository": ValueESRepository(es_client_manager.client),
                    "embedding_client": embedding_client_manager.client,
                },
            )
    finally:
        await asyncio.gather(meta_client_manager.close(), dw_client_manager.close(),
                             qdrant_client_manager.close(), es_client_manager.close())


async def main() -> None:
    # 运行前先展示问题，便于对应随后出现的召回、SQL 和核实日志。
    print_json("===== 用户问题 =====", {"query": QUESTION})
    result = await run_question(QUESTION)

    print("===== Node 1：Intent Detection =====")
    print_json("输出：", {"intent": result.get("intent"), "intent_reason": result.get("intent_reason", "")})
    if result.get("intent") != "anomaly_analysis":
        print("本次没有分流到异动分析 Agent，不展示变化核实节点。")
        return

    state = result["agent_result"]
    request = state["analysis_request"]
    metric = state["metric_context"]
    analysis_result = state["analysis_result"]

    print("===== Node 2：Parse Request =====")
    print_json("输入：", {"raw_question": QUESTION})
    print_json("输出：", request)

    print("===== Node 3：Load Metric Context =====")
    print_json("输入：", {"analysis_request": request})
    print_json("输出：", metric)

    print("===== Node 4：Query Overall =====")
    print_json("输入：", {"analysis_request": request, "metric_context": metric,
                         "analysis_plan": state.get("analysis_plan")})
    print_json("输出：", analysis_result)

    print("===== Node 5：Verify Change =====")
    print_json("输入：", {"analysis_request": request, "metric_context": metric,
                         "analysis_result": analysis_result})
    print_json("输出：", state["change_verification"])


if __name__ == "__main__":
    asyncio.run(main())
