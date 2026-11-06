"""根据解析出的指标选择 Main_RootCause_Agent Skill。"""

from app.agent.anomaly_agent.Main_RootCause_Agent.skills.registry import get_skill_by_metric
from app.agent.anomaly_agent.Main_RootCause_Agent.state import RootCauseState


# 异动分析业务域固定的 Skill 匹配范围。这里不从 Orchestrator State
# 读取 intent，避免子 Agent 与上层分流逻辑产生隐式耦合。
ANOMALY_ANALYSIS_INTENT = "anomaly_analysis"


def select_skill(state: RootCauseState) -> dict[str, object]:
    """根据 TaskRequest 匹配 Skill，并把 Skill 标识写回 State。"""

    task = state.get("task_request")
    if task is None:
        raise ValueError("选择 Skill 前必须先完成问题解析")

    metrics = [metric.strip() for metric in task.metrics if metric.strip()]
    if len(metrics) == 0:
        raise ValueError("任务中没有明确指标，无法选择单一 Skill")
    if len(metrics) > 1:
        raise ValueError(
            f"任务包含多个指标 {metrics}，当前 Skill 机制无法确认唯一处理 Skill"
        )

    skill = get_skill_by_metric(metrics[0], intent=ANOMALY_ANALYSIS_INTENT)
    return {"selected_skill_name": skill.name}

