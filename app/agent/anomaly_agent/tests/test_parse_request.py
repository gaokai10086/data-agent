"""在 PyCharm/终端中验证“意图识别 → 异动分流 → 问题解析”。

只需修改 QUESTION，不需要启动 Web 服务。它调用应用当前使用的 Orchestrator Graph，
不是直接绕过意图分流调用异动 Graph。

当识别为异动归因时，当前异动分支只解析问题，不会查询销售额。
若改成普通问数等其他意图，Orchestrator 会进入对应分支，可能需要其他基础设施。
"""

from __future__ import annotations

import asyncio
import json

from app.agent.orchestrator.graph import orchestrator_graph


QUESTION = "为什么2025年7月销售额比2025年5月下降了？"


async def main() -> None:
    # 与 Web 使用同一张 Orchestrator Graph：先识别意图，再选择子 Agent。
    # context 不指定模型配置时，意图节点和异动解析节点都使用系统默认模型。
    result = await orchestrator_graph.ainvoke(
        {"query": QUESTION},
        context={},
    )

    print("===== 原始问题 =====")
    print(result["query"])
    print("===== 意图识别与分流 =====")
    print(f"intent: {result['intent']}")
    print(f"reason: {result.get('intent_reason', '')}")

    if result["intent"] != "anomaly_analysis":
        print("本次没有分流到异动归因 Agent，因此没有异动 AnalysisRequest。")
        return

    # Orchestrator 的 agent_result 是子 Graph 的最终 State。
    anomaly_result = result["agent_result"]
    print("===== 校验后的 AnalysisRequest =====")
    print(json.dumps(anomaly_result["analysis_request"].model_dump(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
