"""真实链路入口：修改 QUESTION 后运行；调用模型、检索和数据库。"""
import asyncio

from app.agent.anomaly_agent.tests.test_verify_change import print_json, run_question


QUESTION = "为什么2025年7月上海销售额比2025年6月下降了？"


async def main():
    print_json("用户问题：", {"query": QUESTION})
    result = await run_question(QUESTION)
    state = result.get("agent_result", {})
    print_json("变化核实：", state.get("change_verification"))
    print_json("候选策略：", state.get("analysis_strategy"))
    report = state.get("change_analysis_report")
    if report is not None:
        print("执行路径（scope_depth 大于 0 表示下钻）：")
        for observation in report.observations:
            scope = "、".join(f"{f.dimension}={f.value}" for f in observation.scope_filters)
            print(f"{observation.observation_id} | scope_depth={observation.scope_depth} | "
                  f"范围：{scope} | 分析：{observation.action.dimension_column} | "
                  f"状态：{observation.status}")
    print_json("分析报告（包含决策、观察和停止原因）：", state.get("change_analysis_report"))


if __name__ == "__main__":
    asyncio.run(main())
