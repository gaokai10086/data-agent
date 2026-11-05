"""根据解析出的指标选择 Main_RootCause_Agent Skill。"""

from app.agent.anomaly_agent.Main_RootCause_Agent.skills.registry import get_skill_by_metric
from app.agent.anomaly_agent.Main_RootCause_Agent.state import RootCauseState


def select_skill(state: RootCauseState) -> dict[str, object]:
    """根据解析出的指标匹配 Skill，并把 Skill 标识写回 State。"""

    request = state.get("analysis_request")
    if request is None:
        raise ValueError("选择 Skill 前必须先完成问题解析")

    intent = state.get("intent")
    if not intent:
        raise ValueError("选择 Skill 需要 Orchestrator 提供 intent")

    skill = get_skill_by_metric(request.metric, intent=intent)
    return {"selected_skill_name": skill.name}

