"""可直接运行的 Orchestrator → Main_RootCause_Agent 验证脚本。

在 PyCharm 中右键本文件，选择 Run 即可，不依赖 pytest。
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from app.agent.anomaly_agent.Main_RootCause_Agent.graph import main_root_cause_agent
from app.agent.orchestrator.nodes.intent_detection import intent_detection


def run_intent_detection(question: str, mocked_intent: str) -> dict[str, object]:
    """使用固定模型响应，验证 Orchestrator 的意图解析逻辑。"""

    events: list[dict[str, object]] = []
    runtime = SimpleNamespace(stream_writer=events.append)
    model_reply = AIMessage(content=json.dumps({
        "intent": mocked_intent,
        "reason": "验证脚本中的固定响应",
        "keywords": [],
    }, ensure_ascii=False))
    fake_llm = RunnableLambda(lambda _: model_reply)

    with patch(
        "app.agent.orchestrator.nodes.intent_detection.get_context_llm",
        return_value=fake_llm,
    ):
        return asyncio.run(intent_detection({"query": question}, runtime))


def verify_anomaly_question_flow() -> None:
    """验证异动问题经过意图识别后可以进入根因 Agent。"""

    question = "为什么北京地区 2025 年 7 月销售额比 6 月下降？"
    intent_result = run_intent_detection(question, "anomaly_analysis")
    assert intent_result["intent"] == "anomaly_analysis"

    root_cause_state = main_root_cause_agent.invoke({
        "raw_question": question,
        "intent": intent_result["intent"],
    })
    assert root_cause_state["raw_question"] == question


def verify_non_anomaly_question_flow() -> None:
    """验证普通查询不会被标记成异动分析。"""

    question = "查询北京地区 2025 年 7 月销售额"
    intent_result = run_intent_detection(question, "query_database")
    assert intent_result["intent"] == "query_database"


def main() -> None:
    verify_anomaly_question_flow()
    print("[通过] 异动问题：Orchestrator → Main_RootCause_Agent")

    verify_non_anomaly_question_flow()
    print("[通过] 普通查询：没有被标记为 anomaly_analysis")

    print("全部验证通过")


if __name__ == "__main__":
    main()

