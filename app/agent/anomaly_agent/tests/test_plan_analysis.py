"""真实链路运行入口：修改 QUESTION，运行 main，展示变化核实及策略节点输入输出。"""
from __future__ import annotations


# 与同目录节点测试一致：先修改用户问题，再直接运行本文件。
QUESTION = "为什么2025年北京市7月销售额比2025年6月下降了？"


import asyncio


async def main() -> None:
    # 复用同目录真实链路资源管理，避免复制数据库初始化和连接关闭逻辑。
    from app.agent.anomaly_agent.tests.test_verify_change import print_json, run_question

    print_json("===== 用户问题 =====", {"query": QUESTION})
    result = await run_question(QUESTION)
    if result.get("intent") != "anomaly_analysis":
        print_json("未进入异动分析：", {"intent": result.get("intent")})
        return
    state = result["agent_result"]
    print_json("===== 变化核实 =====", state["change_verification"])
    if state["change_verification"].decision != "continue":
        print("变化核实已停止，本次不执行分析策略节点。")
        return
    print("===== Node 6：Plan Analysis =====")
    print_json("输入：", {
        "analysis_request": state["analysis_request"], "metric_context": state["metric_context"],
        "analysis_result": state["analysis_result"], "change_verification": state["change_verification"],
    })
    print_json("输出：", state["analysis_strategy"])


if __name__ == "__main__":
    asyncio.run(main())
