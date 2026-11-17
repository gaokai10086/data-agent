"""异动报告的确定性文字解读；只消费已验证结构化证据，不执行查询。"""

from __future__ import annotations

from typing import Any


def _amount(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def build_narratives(overall, decomposition, paths, diagnosis) -> dict[str, Any]:
    """生成可被 LLM 替换的事实摘要；当前使用安全 fallback，避免数字幻觉。"""
    result: dict[str, Any] = {"source": "fallback", "drilldown_insights": {}}
    if overall:
        change = _amount(overall.get("change_amount"))
        rate = _amount(overall.get("change_rate"))
        base = (
            f"{overall.get('metric', '指标')}在 {overall.get('comparison_period') or '对比周期'} 为 {overall.get('comparison_value')}，"
            f"在 {overall.get('target_period') or '目标周期'} 为 {overall.get('current_value')}；"
            f"变化金额 {change:,.2f}，变化率 {rate * 100:.2f}%。" if change is not None and rate is not None else
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
        if amount is not None:
            rate_text.append(f"变化金额 {amount:,.2f}")
        if overall_rate is not None:
            rate_text.append(f"占总体变化 {overall_rate * 100:.2f}%")
        if parent_rate is not None:
            rate_text.append(f"占直接父级变化 {parent_rate * 100:.2f}%")
        result["drilldown_insights"][path.get("task_id")] = (
            f"本层按两期真实数据完成对账。{selected}是当前层下降贡献最大的成员，"
            f"{'，'.join(rate_text)}；因此将该成员作为下一层分析范围。"
            "该结论描述的是统计贡献，不代表已经确认具体业务原因。"
        )
        if selected is not None:
            selected_item = next((m for m in (path.get("attributions") or []) if m.get("group_value") == selected), {})
            amount = _amount(selected_item.get("change_amount"))
            change_rate = _amount(selected_item.get("change_rate"))
            overall_rate = _amount(selected_item.get("overall_contribution_rate", selected_item.get("net_contribution_rate")))
            parent_rate = _amount(selected_item.get("parent_contribution_rate"))
            amount_text = f"变化 {amount:,.2f}" if amount is not None else "变化不可计算"
            overall_text = f"整体贡献 {overall_rate * 100:.2f}%" if overall_rate is not None else "整体贡献不可计算"
            parent_text = f"父级贡献 {parent_rate * 100:.2f}%" if parent_rate is not None else "父级贡献不可计算"
            change_rate_text = f"环比变化率 {change_rate * 100:.2f}%" if change_rate is not None else "环比变化率不可计算"
            selected_chain.append(
                f"{str(path.get('dimension_column') or '维度').rsplit('.', 1)[-1]}={selected}"
                f"（{amount_text}，{change_rate_text}，{overall_text}，{parent_text}）"
            )
    if selected_chain:
        result["overview_insight"] += " 逐层对账后，已验证的主要异常范围为：" + " → ".join(selected_chain) + "。"
        result["overview_insight"] += " 该范围表示各层在总体变化中的统计贡献，不能将不同层级金额重复相加。"
    if diagnosis:
        result["overview_insight"] += " 业务诊断结论仅采用已关联真实证据的发现，未验证假设不会视为业务原因。"
    result["diagnosis_insight"] = "业务诊断中的假设必须以对应 evidence_id 的真实证据为准。" if diagnosis else None
    return result
