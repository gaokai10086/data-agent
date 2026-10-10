"""在 PyCharm/终端中验证“意图识别 → 异动分流 → 问题解析 → 指标上下文”。

"""

from __future__ import annotations

import asyncio
import json

from app.clients.mysql_client import meta_client_manager
from app.agent.orchestrator.graph import orchestrator_graph
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository


QUESTION = "为什么2025年7月销售额比2025年5月下降了？"


async def main() -> None:
    # 意图节点和异动解析节点使用系统默认模型；Meta Repository 使用真实元数据库。
    meta_client_manager.init()
    try:
        async with meta_client_manager.get_session() as session:
            context = {
                "meta_mysql_repository": MetaMySQLRepository(session),
            }
            # 与 Web 使用同一张 Orchestrator Graph：先识别意图，再选择子 Agent。
            result = await orchestrator_graph.ainvoke(
                {"query": QUESTION},
                context=context,
            )
    finally:
        await meta_client_manager.close()

    print("===== 原始问题 =====")
    print(result["query"])
    print("===== 意图识别与分流 =====")
    print(f"intent: {result['intent']}")
    print(f"reason: {result.get('intent_reason', '')}")

    if result["intent"] != "anomaly_analysis":
        print("本次没有分流到异动归因 Agent，因此没有异动 AnalysisRequest。")
        return

    # Orchestrator 的 agent_result 是异动子 Graph 的最终 State。
    anomaly_result = result["agent_result"]
    print("===== 校验后的 AnalysisRequest =====")
    print(json.dumps(anomaly_result["analysis_request"].model_dump(), ensure_ascii=False, indent=2))
    print("===== MetricContext（共享 Metadata） =====")
    print(json.dumps(anomaly_result["metric_context"].model_dump(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
