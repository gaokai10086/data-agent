"""Main_RootCause_Agent 可加载的 Skill 配置。"""

from app.agent.anomaly_agent.Main_RootCause_Agent.skills.registry import (
    get_skill_by_intent,
    load_skill,
)
from app.agent.anomaly_agent.Main_RootCause_Agent.skills.base import BaseSkill
from app.agent.anomaly_agent.Main_RootCause_Agent.skills.SalesAmountAnomalySkill import (
    SalesAmountAnomalySkill,
)

__all__ = [
    "BaseSkill",
    "SalesAmountAnomalySkill",
    "get_skill_by_intent",
    "load_skill",
]

