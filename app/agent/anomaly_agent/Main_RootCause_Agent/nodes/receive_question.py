"""接收 Orchestrator 已分流的异动问题。"""

from app.agent.anomaly_agent.Main_RootCause_Agent.state import RootCauseState


def receive_question(state: RootCauseState) -> dict[str, object]:
    """校验问题存在，并保留 Orchestrator 传入的原始文本。

    该节点不做意图识别、问题改写、指标解析或数据库查询。空白问题
    直接失败，避免后续节点在无效输入上产生误导性的分析结果。
    """

    raw_question = state.get("raw_question", "")
    if not isinstance(raw_question, str) or not raw_question.strip():
        raise ValueError("Main_RootCause_Agent 接收的问题不能为空")
    return {}

