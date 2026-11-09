"""根据提取指标匹配已启用的 Main_RootCause_Agent Skill。"""

from app.agent.anomaly_agent.Main_RootCause_Agent.skills.registry import match_skill
from app.agent.anomaly_agent.Main_RootCause_Agent.state import RootCauseState


def select_skill(state: RootCauseState) -> dict[str, object]:
    """只允许 matched 进入 load_skill，其余状态受控返回。"""
    metrics = state.get("extracted_metrics", [])
    question = state.get("raw_question", "")
    result = match_skill(metrics, question)
    output: dict[str, object] = {
        "skill_result": {
            "status": result.status,
            "reason": result.reason,
            "skill_name": result.selected_skill_name,
        },
    }
    return output
