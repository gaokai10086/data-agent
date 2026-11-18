"""多路径归因报告的展示模型；只读取已对账的报告分组。"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from html import escape


def branch_children(sections: list[dict]) -> dict[tuple[str, str], dict]:
    """以真实 parent_task_id 和末级父成员建立导航关系。"""
    by_id = {section.get("task_id"): section for section in sections if section.get("task_id")}
    children = {}
    for section in sections:
        parent = by_id.get(section.get("parent_task_id"))
        scope = section.get("parent_scope") or []
        if not parent or not scope or section.get("depth") != parent.get("depth", 0) + 1:
            continue
        member = scope[-1]
        if member.get("dimension_column") != parent.get("dimension_column"):
            continue
        name = member.get("member_value")
        if name not in (parent.get("selected_members") or []):
            continue
        if scope[:-1] != (parent.get("parent_scope") or []):
            continue
        if not all((section.get("reconciliation") or {}).get(key) is True for key in
                   ("comparison_matches", "current_matches", "change_matches")):
            continue
        children[(parent["task_id"], name)] = section
    return children


def decline_chart_options(section: dict) -> dict:
    """金额保持真实符号；柱长使用绝对值，颜色表示是否继续下钻。"""
    selected = set(section.get("selected_members") or [])
    rows = []
    for item in section.get("rows") or []:
        try:
            change = Decimal(str(item.get("变化金额")))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if not change.is_finite():
            continue
        name = item.get("成员")
        rows.append((str(name) if name is not None else "未标注成员", change, name in selected))
    rows.sort(key=lambda item: (0 if item[1] < 0 else 1, -abs(item[1]), item[0]))
    colors = {"selected": "#2B63DA", "unselected": "#AEB5C2", "offset": "#2F9C78"}
    data = []
    for name, change, is_selected in rows:
        kind = "offset" if change > 0 else "selected" if is_selected else "unselected"
        actual = f"{change / Decimal('10000'):+,.2f}"
        data.append({"value": float(abs(change) / Decimal("10000")),
                     "actual": actual,
                     "name": name, "kind": kind,
                     "tooltip": {"formatter": f"{escape(name)}<br/>变化金额：{actual} 万元"},
                     "label": {"show": True, "position": "right", "formatter": f"{actual} 万"},
                     "itemStyle": {"color": colors[kind], "borderRadius": [0, 4, 4, 0]}})
    return {
        "animation": False,
        "grid": {"left": 10, "right": 60, "top": 12, "bottom": 35, "containLabel": True},
        "tooltip": {"trigger": "item"},
        "xAxis": {"type": "value", "name": "变化幅度（万元）", "min": 0,
                  "axisLabel": {"formatter": "{value}"}},
        "yAxis": {"type": "category", "inverse": True, "data": [item[0] for item in rows],
                  "axisTick": {"show": False}},
        "series": [{"type": "bar", "barMaxWidth": 24, "data": data,
                    "label": {"show": True, "position": "right", "color": "#667085"}}],
    }
