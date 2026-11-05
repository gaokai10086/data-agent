"""可直接在 PyCharm 中运行的 Skill 选择节点验证脚本。"""

from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.select_skill import select_skill
from app.agent.anomaly_agent.Main_RootCause_Agent.skills.registry import UnsupportedSkillError
from app.agent.anomaly_agent.schemas import AnalysisRequest


def build_request(metric: str = "销售额") -> AnalysisRequest:
    return AnalysisRequest(
        metric=metric,
        target_period="2025-07",
        comparison_period="2025-06",
        comparison_type="mom",
    )


def verify_sales_amount_skill_selected_by_metric() -> None:
    state = select_skill({
        "analysis_request": build_request(),
        "intent": "anomaly_analysis",
    })
    assert state["selected_skill_name"] == "sales_amount_anomaly"


def verify_missing_parse_is_rejected() -> None:
    try:
        select_skill({"raw_question": "为什么销售额下降？", "intent": "anomaly_analysis"})
    except ValueError as exc:
        assert "完成问题解析" in str(exc)
    else:
        raise AssertionError("缺少解析结果时没有拒绝选择 Skill")


def verify_missing_intent_is_rejected() -> None:
    try:
        select_skill({"analysis_request": build_request()})
    except ValueError as exc:
        assert "Orchestrator 提供 intent" in str(exc)
    else:
        raise AssertionError("缺少 intent 时没有拒绝选择 Skill")


def verify_unsupported_metric_is_rejected() -> None:
    try:
        select_skill({"analysis_request": build_request("利润"), "intent": "anomaly_analysis"})
    except UnsupportedSkillError:
        return
    raise AssertionError("尚未配置利润 Skill，却允许选择了 Skill")


def main() -> None:
    verify_sales_amount_skill_selected_by_metric()
    print("[通过] 解析出的销售额指标选择了 sales_amount_anomaly")

    verify_missing_parse_is_rejected()
    print("[通过] 缺少解析结果时拒绝选择 Skill")

    verify_missing_intent_is_rejected()
    print("[通过] 缺少 intent 时拒绝选择 Skill")

    verify_unsupported_metric_is_rejected()
    print("[通过] 尚未支持的指标问题会被拒绝")

    print("全部验证通过")


if __name__ == "__main__":
    main()
