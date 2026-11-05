"""可直接在 PyCharm 中运行的 Skill 配置验证脚本。"""

from app.agent.anomaly_agent.Main_RootCause_Agent.skills.registry import (
    UnsupportedSkillError,
    get_skill_by_intent,
    load_skill,
)
from app.agent.anomaly_agent.Main_RootCause_Agent.skills.base import BaseSkill


def verify_anomaly_skill_can_be_loaded_by_name() -> None:
    skill = load_skill("sales_amount_anomaly")
    assert isinstance(skill, BaseSkill)
    assert skill.name == "sales_amount_anomaly"
    assert skill.version == "1.0"


def verify_anomaly_skill_can_be_loaded_by_orchestrator_intent() -> None:
    skill = get_skill_by_intent("anomaly_analysis")
    assert skill.name == "sales_amount_anomaly"
    assert "metric_change_analysis" in skill.capabilities
    assert "销售额" in skill.supported_metrics
    assert "销售额" in skill.load_instructions()


def verify_unsupported_intent_is_rejected() -> None:
    try:
        get_skill_by_intent("query_database")
    except UnsupportedSkillError:
        return
    raise AssertionError("query_database 不应该加载异动分析 Skill")


def main() -> None:
    verify_anomaly_skill_can_be_loaded_by_name()
    print("[通过] 可以按名称加载 SalesAmountAnomalySkill")

    verify_anomaly_skill_can_be_loaded_by_orchestrator_intent()
    print("[通过] 可以根据 Orchestrator intent 加载 Skill")

    verify_unsupported_intent_is_rejected()
    print("[通过] 不支持的 intent 会被拒绝")

    print("全部验证通过")


if __name__ == "__main__":
    main()

