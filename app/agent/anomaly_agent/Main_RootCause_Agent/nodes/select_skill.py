"""根据解析出的指标选择 Main_RootCause_Agent Skill。"""

from app.agent.anomaly_agent.Main_RootCause_Agent.skills.registry import get_skill_by_metric
from app.agent.anomaly_agent.Main_RootCause_Agent.state import RootCauseState


# 异动分析业务域固定的 Skill 匹配范围。这里不从 Orchestrator State
# 读取 intent，避免子 Agent 与上层分流逻辑产生隐式耦合。
ANOMALY_ANALYSIS_INTENT = "anomaly_analysis"


def select_skill(state: RootCauseState) -> dict[str, object]:
    """根据解析出的指标匹配 Skill，并把 Skill 标识写回 State。"""

    request = state.get("analysis_request")
    if request is None:
        raise ValueError("选择 Skill 前必须先完成问题解析")

    skill = get_skill_by_metric(request.metric, intent=ANOMALY_ANALYSIS_INTENT)
    return {"selected_skill_name": skill.name}

