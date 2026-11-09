"""加载并校验已匹配的 Skill。"""

from app.agent.anomaly_agent.Main_RootCause_Agent.skills.registry import (
    load_skill,
    resolve_skill_capabilities,
)
from app.agent.anomaly_agent.Main_RootCause_Agent.state import RootCauseState


def load_skill_node(state: RootCauseState) -> dict[str, object]:
    """从 skill_result 加载并校验；不向 State 写入隐式摘要。"""
    skill_result = state.get("skill_result") or {}
    name = skill_result.get("skill_name")
    if not name:
        raise ValueError("load_skill 需要在 matched 之后调用")

    skill = load_skill(name)
    capabilities = resolve_skill_capabilities(skill)
    if capabilities["unavailable_capabilities"]:
        raise ValueError(
            f"Skill 声明了未注册能力：{capabilities['unavailable_capabilities']}"
        )
    return {}
