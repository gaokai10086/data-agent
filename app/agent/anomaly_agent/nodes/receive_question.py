"""接收经 Orchestrator 分流的原始用户问题。"""

from app.agent.anomaly_agent.state import AnomalyState


def receive_question(state: AnomalyState) -> dict[str, object]:
    """验证从 Orchestrator 收到的问题。

    Graph 输入已经包含 raw_question；本节点不重写它，因此后续节点能拿到
    完整原话。只用 strip() 判断是否全为空白，不改变原始文本。
    此处不猜测指标、时间或维度，也不执行任何数据查询。
    """
    raw_question = state.get("raw_question", "")
    if not raw_question.strip():
        # 输入无效时直接失败，不让后续解析节点拿着空问题继续运行。
        raise ValueError("异动归因问题不能为空")
    # 没有新的业务状态需要写回；raw_question 已经存在于 State 中。
    return {}
