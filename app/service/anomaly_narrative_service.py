"""异动报告的确定性文字解读；只消费已验证结构化证据，不执行查询。"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


def _amount(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _wan(value: Any) -> str:
    try:
        return f"{Decimal(str(value)) / Decimal('10000'):,.2f} 万元"
    except (InvalidOperation, TypeError, ValueError):
        return "金额未知"


def _period(value: Any) -> str:
    if isinstance(value, str) and len(value) == 7 and value[4] == "-":
        return f"{value[:4]} 年 {int(value[5:])} 月"
    return str(value or "目标期")


def _decline_context(members: list[dict], selected: Any) -> str:
    """Describe only observed members in the same reconciled grouping."""
    declines = sorted(
        (item for item in members if (_amount(item.get("change_amount")) or 0) < 0),
        key=lambda item: _amount(item.get("change_amount")) or 0,
    )
    increases = sorted(
        (item for item in members if (_amount(item.get("change_amount")) or 0) > 0),
        key=lambda item: _amount(item.get("change_amount")) or 0,
        reverse=True,
    )
    parts = []
    other = next((item for item in declines if item.get("group_value") != selected), None)
    if other:
        parts.append(f"同层的“{other.get('group_value')}”也下降约 {_wan(abs(_amount(other.get('change_amount'))))}")
    if increases and declines:
        item = increases[0]
        parts.append(f"“{item.get('group_value')}”则增长约 {_wan(_amount(item.get('change_amount')))}，对净下降形成抵消")
    return "；".join(parts)


def build_narratives(overall, decomposition, paths, diagnosis, dimension_names: dict[str, str] | None = None) -> dict[str, Any]:
    """生成可被 LLM 替换的事实摘要；当前使用安全 fallback，避免数字幻觉。"""
    result: dict[str, Any] = {"source": "fallback", "drilldown_insights": {}}
    if overall:
        change = _amount(overall.get("change_amount"))
        rate = _amount(overall.get("change_rate"))
        base = (
            f"{_period(overall.get('target_period'))}，{overall.get('metric', '指标')}从"
            f"{_wan(overall.get('comparison_value'))}降至{_wan(overall.get('current_value'))}，"
            f"减少约 {_wan(abs(change))}，降幅 {abs(rate) * 100:.2f}%。" if change is not None and change < 0 and rate is not None else
            f"{_period(overall.get('target_period'))}，{overall.get('metric', '指标')}由"
            f"{_wan(overall.get('comparison_value'))}变为{_wan(overall.get('current_value'))}，"
            f"变化 {_wan(change)}，变化率 {rate * 100:.2f}%。" if change is not None and rate is not None else
            "总体变化已完成真实周期核对。"
        )
        result["overview_insight"] = base
    else:
        result["overview_insight"] = "暂无已验证的总体变化证据。"
    if decomposition and decomposition.get("components"):
        components = sorted(decomposition["components"], key=lambda x: abs(_amount(x.get("contribution_amount")) or 0), reverse=True)
        top = components[0]
        factor_lines = []
        for item in components:
            rate = _amount(item.get("contribution_rate"))
            amount = _amount(item.get("contribution_amount"))
            if rate is not None and amount is not None:
                factor_lines.append(f"{item.get('factor_name')}贡献 {amount:,.2f}（{rate * 100:.2f}%）")
        result["decomposition_insight"] = (
            f"指标拆解显示，{top.get('factor_name')}是贡献金额最大的统计因素。"
            f"各因素贡献为：{'；'.join(factor_lines)}。"
            "这说明销售额变化在指标构成上主要由上述因素解释，但统计贡献不等同于已经确认的业务因果。"
        )
    else:
        result["decomposition_insight"] = "本次未进行指标拆解，暂无订单量与客单价因素结论。"
    selected_chain = []
    root_members = []
    for path in sorted(paths or [], key=lambda item: item.get("depth", 0)):
        members = path.get("attributions") or []
        selected = path.get("selected_member")
        if selected is None and members:
            selected = min(members, key=lambda x: _amount(x.get("change_amount")) or 0).get("group_value")
        selected_item = next((m for m in members if m.get("group_value") == selected), None)
        amount = _amount((selected_item or {}).get("change_amount"))
        overall_rate = _amount((selected_item or {}).get("overall_contribution_rate", (selected_item or {}).get("net_contribution_rate")))
        parent_rate = _amount((selected_item or {}).get("parent_contribution_rate"))
        rate_text = []
        comparison_value = (selected_item or {}).get("comparison_value")
        current_value = (selected_item or {}).get("current_value")
        if comparison_value is not None and current_value is not None:
            rate_text.append(f"由 {_wan(comparison_value)}变为 {_wan(current_value)}")
        if amount is not None:
            rate_text.append(f"变化金额 {amount:,.2f}")
        if overall_rate is not None:
            rate_text.append(f"占总体变化 {overall_rate * 100:.2f}%")
        if parent_rate is not None:
            rate_text.append(f"占直接父级变化 {parent_rate * 100:.2f}%")
        scope = path.get("parent_scope") or []
        scope_text = "、".join(f"{(dimension_names or {}).get(item.get('dimension_column')) or str(item.get('dimension_column') or '维度').rsplit('.', 1)[-1]}“{item.get('member_value')}”" for item in scope)
        lead = f"在{scope_text}范围内，" if scope_text else "从本层分组看，"
        context_text = _decline_context(members, selected)
        if selected_item is not None:
            result["drilldown_insights"][path.get("task_id")] = (
                f"{lead}“{selected}”是重点下降成员，{'，'.join(rate_text)}。"
                + (f"{context_text}。" if context_text else "")
            )
        reconciled = path.get("reconciliation") or {}
        if selected is not None and all(reconciled.get(key) is True for key in ("comparison_matches", "current_matches", "change_matches")):
            if not selected_chain:
                root_members = members
            selected_item = next((m for m in (path.get("attributions") or []) if m.get("group_value") == selected), {})
            amount = _amount(selected_item.get("change_amount"))
            change_rate = _amount(selected_item.get("change_rate"))
            overall_rate = _amount(selected_item.get("overall_contribution_rate", selected_item.get("net_contribution_rate")))
            parent_rate = _amount(selected_item.get("parent_contribution_rate"))
            selected_chain.append(
                ((dimension_names or {}).get(path.get('dimension_column')) or str(path.get('dimension_column') or '维度').rsplit('.', 1)[-1],
                 selected, amount, overall_rate)
            )
    if selected_chain:
        root = selected_chain[0]
        terminal = selected_chain[-1]
        description = f"从已对账的维度结果看，{root[0]}“{root[1]}”是当前路径的重点下降范围"
        if root[2] is not None and root[2] < 0:
            description += f"，销售额减少约 {_wan(abs(root[2]))}"
        if root[3] is not None:
            description += f"，占总体净变化的 {root[3] * 100:.2f}%"
        if len(selected_chain) > 1:
            intermediate = "、".join(f"{item[0]}“{item[1]}”" for item in selected_chain[1:-1])
            if intermediate:
                description += f"。沿这一范围继续分析，异常经过{intermediate}，最终定位到{terminal[0]}“{terminal[1]}”"
            else:
                description += f"，进一步定位到{terminal[0]}“{terminal[1]}”"
            if terminal[2] is not None and terminal[2] < 0:
                description += f"，该局部成员减少约 {_wan(abs(terminal[2]))}"
        result["overview_insight"] += "\n\n" + description + "。"
        root_context = _decline_context(root_members, root[1])
        if root_context:
            result["overview_insight"] += f"{root_context}。"
    if decomposition and decomposition.get("components") and (decomposition.get("reconciliation") or {}).get("matches") is True:
        ranked = sorted(decomposition["components"], key=lambda item: abs(_amount(item.get("contribution_amount")) or 0), reverse=True)
        top = ranked[0]
        top_amount = _amount(top.get("contribution_amount"))
        top_rate = _amount(top.get("contribution_rate"))
        factor_text = f"指标拆解显示，{top.get('factor_name')}是影响更大的统计因素"
        if top_amount is not None:
            factor_text += f"，对应贡献约 {_wan(abs(top_amount))}"
        if top_rate is not None:
            factor_text += f"，占总体净变化的 {top_rate * 100:.2f}%"
        if len(ranked) > 1:
            factor_text += f"；{ranked[1].get('factor_name')}也有贡献，但幅度相对较小"
        result["overview_insight"] += "\n\n" + factor_text + "。"
    if diagnosis:
        supported = next((item for item in diagnosis.get("hypotheses") or []
                          if item.get("status") == "supported" and item.get("supporting_evidence_ids") and item.get("statement")), None)
        if supported:
            result["overview_insight"] += f"\n\n进一步的业务诊断支持：{supported['statement']}"
    result["diagnosis_insight"] = "业务诊断中的假设必须以对应 evidence_id 的真实证据为准。" if diagnosis else None
    return result
