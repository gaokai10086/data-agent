"""RootCause Observation 到 Web 事件/报告的适配层。

该模块只做展示协议转换，不执行 SQL、不计算业务指标，也不修改专业 Agent
的结果 Schema。所有图表数据必须来自已完成的真实 Observation。
"""

from __future__ import annotations

import hashlib
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from app.service.anomaly_narrative_service import build_narratives


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _request_id(state: dict[str, Any]) -> str | None:
    for item in reversed(state.get("observations") or []):
        outcome = item.get("inspection_outcome") or {}
        if outcome.get("request_id"):
            return outcome["request_id"]
        result = item.get("business_diagnosis_result") or item.get("attribution_result") or item.get("decomposition_result") or {}
        if result.get("request_id"):
            return result["request_id"]
    return None


def _event_id(request_id: str | None, event_type: str, key: str) -> str:
    raw = f"{request_id or 'unknown'}|{event_type}|{key}"
    return "evt_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _base_event(event_type: str, stage: str, state: dict[str, Any], key: str, **payload: Any) -> dict[str, Any]:
    request_id = _request_id(state)
    return {
        "type": event_type,
        "event_id": _event_id(request_id, event_type, key),
        "request_id": request_id,
        "stage": stage,
        **_jsonable(payload),
    }


def _overall_payload(observation: dict[str, Any]) -> dict[str, Any] | None:
    evidence = observation.get("evidence") or {}
    if evidence.get("current_value") is None and evidence.get("comparison_value") is None:
        return None
    return {
        "metric": evidence.get("metric") or "销售额",
        "target_period": evidence.get("target_period"),
        "comparison_period": evidence.get("comparison_period"),
        "current_value": evidence.get("current_value"),
        "comparison_value": evidence.get("comparison_value"),
        "change_amount": evidence.get("change_amount"),
        "change_rate": evidence.get("change_rate"),
    }


def _reconciliation_passed(value: dict[str, Any] | None, *, dimension: bool = False) -> bool:
    """Only expose charts after the tool's explicit reconciliation flags pass."""
    if not isinstance(value, dict):
        return False
    keys = ("comparison_matches", "current_matches", "change_matches") if dimension else ("matches",)
    return all(value.get(key) is True for key in keys)


def _dimension_names(state: dict[str, Any]) -> dict[str, str]:
    """Use the published MetricContext carried by ChangeInspection, not guessed translations."""
    for observation in state.get("observations") or []:
        context = (observation.get("inspection_outcome") or {}).get("metric_context") or {}
        dimensions = context.get("dimensions") or []
        if dimensions:
            return {item["column"]: item["name"] for item in dimensions if item.get("column") and item.get("name")}
    return {}


def _dimension_chart(path: dict[str, Any], names: dict[str, str] | None = None) -> dict[str, Any] | None:
    if not _reconciliation_passed(path.get("reconciliation"), dimension=True):
        return None
    rows = []
    for item in path.get("attributions") or []:
        row = {
            "成员": item.get("group_value"),
            "对比值": item.get("comparison_value"),
            "当前值": item.get("current_value"),
            "变化金额": item.get("change_amount"),
            "整体贡献率": item.get("overall_contribution_rate", item.get("net_contribution_rate")),
        }
        if path.get("depth", 0) > 0:
            row["父级贡献率"] = item.get("parent_contribution_rate")
        rows.append(row)
    if not rows:
        return None
    dimension = path.get("dimension_column") or "维度"
    dimension_label = (names or {}).get(dimension) or str(dimension).rsplit(".", 1)[-1]
    return {
        "chart_type": "bar",
        "title": f"{dimension_label}成员销售额变化",
        "data": rows,
        "x_column": "成员",
        "y_columns": ["变化金额"],
        "metric_semantics": {"变化金额": {"display_type": "amount", "value_scale": "absolute", "axis": "left"}},
        "presentation": {"orientation": "horizontal", "sort": "asc"},
        "dimension_column": dimension,
        "dimension_name": dimension_label,
        "reconciliation": path.get("reconciliation"),
        "depth": path.get("depth", 0),
        "parent_scope": path.get("parent_scope") or [],
        "selected_member": path.get("selected_member"),
        "selected_members": path.get("selected_members") or [],
        "actual_decline_coverage": path.get("actual_decline_coverage"),
        "uncovered_decline_amount": path.get("uncovered_decline_amount"),
    }


def _dimension_display_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Format table cells only; charts and evidence retain their numeric precision."""
    formatted = []
    for row in rows:
        display = {}
        for key, value in row.items():
            if value is None or key == "成员":
                display[key] = value
                continue
            try:
                number = Decimal(str(value))
                if not number.is_finite():
                    raise InvalidOperation
                display[key] = f"{number * 100:.2f}%" if key.endswith("贡献率") else f"{number:,.2f}"
            except (InvalidOperation, TypeError, ValueError):
                display[key] = value
        formatted.append(display)
    return formatted


def _dimension_summary(paths: list[dict[str, Any]]) -> str | None:
    """生成仅基于真实归因金额的展示摘要，并明确其统计性质。"""
    candidates = []
    for path in paths:
        if path.get("depth", 0) != 0:
            continue
        if not _reconciliation_passed(path.get("reconciliation"), dimension=True):
            continue
        for item in path.get("attributions") or []:
            amount = item.get("change_amount")
            if amount is None:
                continue
            try:
                numeric = float(amount)
            except (TypeError, ValueError):
                continue
            candidates.append((numeric, path.get("dimension_column") or "维度", item.get("group_value")))
    if not candidates:
        return None
    negative = sorted((item for item in candidates if item[0] < 0), key=lambda item: item[0])
    if not negative:
        return "已完成维度对账，但没有发现下降成员；以上结果表示统计变化贡献，不等同于业务因果。"
    amount, dimension, member = negative[0]
    return f"当前已验证的最大下降成员为 {member}（{dimension}），变化金额约 {amount:,.2f}；这是统计贡献关系，不代表已经确认业务因果。"


def _decomposition_chart(result: dict[str, Any]) -> dict[str, Any] | None:
    if not _reconciliation_passed(result.get("reconciliation")):
        return None
    rows = [
        {"因素": item.get("factor_name"), "贡献金额": item.get("contribution_amount"), "变化量": item.get("change_amount")}
        for item in result.get("components") or []
    ]
    if not rows:
        return None
    return {
        "chart_type": "bar",
        "title": "指标拆解因素贡献",
        "data": rows,
        "x_column": "因素",
        "y_columns": ["贡献金额"],
        "metric_semantics": {"贡献金额": {"display_type": "amount", "value_scale": "absolute", "axis": "left"}},
        "presentation": {"orientation": "horizontal", "sort": "asc"},
        "reconciliation": result.get("reconciliation"),
    }


def _sql_evidence(value: Any, prefix: str = "") -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"sql", "sql_by_period"}:
                if isinstance(item, dict):
                    for period, sql in item.items():
                        if sql:
                            found.append({"period": period, "sql": str(sql), "source": prefix or key})
                elif item:
                    found.append({"period": None, "sql": str(item), "source": prefix or key})
            else:
                found.extend(_sql_evidence(item, f"{prefix}.{key}" if prefix else key))
    elif isinstance(value, list):
        for item in value:
            found.extend(_sql_evidence(item, prefix))
    return found


def _observation_events(observation: dict[str, Any], state: dict[str, Any]) -> list[dict[str, Any]]:
    action = observation.get("action_name") or "unknown"
    status = observation.get("status")
    events = [_base_event("anomaly_progress", action, state, f"{action}:{observation.get('step')}", status=status, reason=observation.get("reason"))]
    if action == "change_inspection" and status == "ready":
        overall = _overall_payload(observation)
        if overall:
            events.append(_base_event("anomaly_kpi", "change_inspection", state, "overall", overall=overall, completion_status="complete"))
    if action == "dimension_attribution" and status in {"completed", "partial"}:
        result = observation.get("attribution_result") or {}
        names = _dimension_names(state)
        charts = [chart for chart in (_dimension_chart(path, names) for path in result.get("paths") or []) if chart]
        if charts:
            events.append(_base_event("anomaly_chart", "dimension_attribution", state, f"{action}:{observation.get('step')}", charts=charts, completion_status=observation.get("completion_status"), status=status))
        events.append(_base_event("anomaly_sql_result", "dimension_attribution", state, f"sql:{observation.get('step')}", sql_evidence=_sql_evidence(result)))
    if action == "metric_decomposition" and status in {"completed", "partial"}:
        result = observation.get("decomposition_result") or {}
        chart = _decomposition_chart(result)
        if chart:
            events.append(_base_event("anomaly_chart", "metric_decomposition", state, f"{action}:{observation.get('step')}", charts=[chart], completion_status=observation.get("completion_status"), status=status))
        events.append(_base_event("anomaly_sql_result", "metric_decomposition", state, f"sql:{observation.get('step')}", sql_evidence=_sql_evidence(result), total_query_count=(observation.get("evidence") or {}).get("total_query_count", 0)))
    if action == "business_diagnosis" and status in {"completed", "partial", "unavailable", "failed"}:
        result = observation.get("business_diagnosis_result") or {}
        events.append(_base_event("anomaly_diagnosis", "business_diagnosis", state, f"{action}:{observation.get('step')}", diagnosis={
            "status": result.get("status"), "completion_status": result.get("completion_status"),
            "stop_reason": result.get("stop_reason"), "findings": result.get("findings", []),
            "hypotheses": result.get("hypotheses", []), "evidence_ids": result.get("evidence_ids", []),
        }))
    return events


def build_anomaly_report(state: dict[str, Any]) -> dict[str, Any]:
    observations = list(state.get("observations") or [])
    inspection = next((item for item in observations if item.get("action_name") == "change_inspection"), None)
    overall = _overall_payload(inspection or {})
    charts: list[dict[str, Any]] = []
    sql_evidence: list[dict[str, Any]] = []
    diagnosis: dict[str, Any] | None = None
    dimension_paths: list[dict[str, Any]] = []
    dimension_names = _dimension_names(state)
    decomposition_result: dict[str, Any] | None = None
    statuses = []
    stage_status = {"change_inspection": "pending", "metric_decomposition": "pending", "dimension_attribution": "pending", "business_diagnosis": "pending"}
    for item in observations:
        statuses.append(item.get("status"))
        action = item.get("action_name")
        if action in stage_status:
            item_status = item.get("status")
            stage_status[action] = "completed" if item_status == "completed" or (action == "change_inspection" and item_status == "ready") else ("failed" if item_status in {"failed", "blocked"} else "partial")
        if action == "dimension_attribution":
            result = item.get("attribution_result") or {}
            dimension_paths.extend(result.get("paths") or [])
            charts.extend(chart for chart in (_dimension_chart(path, dimension_names) for path in result.get("paths") or []) if chart)
            sql_evidence.extend(_sql_evidence(result))
        elif action == "metric_decomposition":
            result = item.get("decomposition_result") or {}
            decomposition_result = result
            chart = _decomposition_chart(result)
            if chart:
                charts.append(chart)
            sql_evidence.extend(_sql_evidence(result))
        elif action == "business_diagnosis":
            diagnosis = item.get("business_diagnosis_result") or {}
            sql_evidence.extend(_sql_evidence(diagnosis))
    if inspection and inspection.get("status") == "rejected":
        status, completion = "rejected", "complete"
        charts = []
        diagnosis = None
        sql_evidence = []
        rejection_reason = inspection.get("reason") or state.get("stop_reason") or "rejected"
        current_value = (overall or {}).get("current_value")
        comparison_value = (overall or {}).get("comparison_value")
        if current_value is not None and comparison_value is not None:
            summary = (
                f"实际销售额从 {comparison_value} 变为 {current_value}，"
                "实际方向为下降，不支持问题中‘上涨’的前提，分析已停止。"
            )
        else:
            summary = "变化检验未支持用户声明的方向，分析已停止。"
    elif any(status == "failed" for status in statuses):
        status, completion = "failed", "partial"
        rejection_reason = None
        summary = None
    elif any(status in {"partial", "unavailable"} for status in statuses):
        status, completion = "partial", "partial"
        rejection_reason = None
        summary = _dimension_summary(dimension_paths)
    elif diagnosis and diagnosis.get("status") == "completed":
        status, completion = "completed", "complete"
        rejection_reason = None
        summary = _dimension_summary(dimension_paths)
    else:
        status, completion = "partial", "partial"
        rejection_reason = None
        summary = _dimension_summary(dimension_paths)
    narratives = build_narratives(overall, decomposition_result, dimension_paths, diagnosis, dimension_names)
    decomposition_section = None
    if decomposition_result:
        decomposition_section = {
            "status": decomposition_result.get("status"),
            "completion_status": decomposition_result.get("completion_status"),
            "formula": decomposition_result.get("formula"),
            "components": decomposition_result.get("components") or [],
            "reconciliation": decomposition_result.get("reconciliation"),
            "summary": narratives.get("decomposition_insight"),
            "chart_config": _decomposition_chart(decomposition_result),
            "sql_evidence": _sql_evidence(decomposition_result),
        }
    drilldown_sections = []
    for path in dimension_paths:
        chart = _dimension_chart(path, dimension_names)
        if chart:
            drilldown_sections.append({
                "task_id": path.get("task_id"), "depth": path.get("depth", 0),
                "dimension_column": path.get("dimension_column"),
                "dimension_name": dimension_names.get(path.get("dimension_column")) or str(path.get("dimension_column") or "维度").rsplit(".", 1)[-1],
                "parent_scope": path.get("parent_scope") or [],
                "parent_task_id": path.get("parent_task_id"),
                "parent_scope_label": "、".join(
                    f"{dimension_names.get(item.get('dimension_column')) or str(item.get('dimension_column') or '维度').rsplit('.', 1)[-1]}={item.get('member_value')}"
                    for item in path.get("parent_scope") or []
                ),
                "selected_member": path.get("selected_member"),
                "selected_members": path.get("selected_members") or [],
                "target_decline_coverage": path.get("target_decline_coverage"),
                "actual_decline_coverage": path.get("actual_decline_coverage"),
                "total_decline_amount": path.get("total_decline_amount"),
                "uncovered_decline_amount": path.get("uncovered_decline_amount"),
                "branch_status": path.get("branch_status"),
                "branch_stop_reason": path.get("branch_stop_reason"),
                "narrative_summary": narratives.get("drilldown_insights", {}).get(path.get("task_id")),
                "drilldown_reason": path.get("stop_reason") or "真实成员贡献排序后继续下钻",
                "chart_config": chart, "rows": chart.get("data") or [],
                "display_rows": _dimension_display_rows(chart.get("data") or []),
                "reconciliation": path.get("reconciliation"),
                "sql_evidence": _sql_evidence(path),
            })
    return {
        "schema_version": "1.0",
        "request_id": _request_id(state),
        "status": status,
        "completion_status": completion,
        "stop_reason": state.get("stop_reason"),
        "rejection_reason": rejection_reason,
        "summary": summary,
        "overall": overall,
        "charts": charts,
        "overview_insight": narratives.get("overview_insight"),
        "narrative_source": narratives.get("source", "fallback"),
        "decomposition_section": decomposition_section,
        "drilldown_sections": drilldown_sections,
        "diagnosis_section": diagnosis,
        "diagnosis": diagnosis,
        "sql_evidence": sql_evidence,
        "observations": observations,
        "stage_status": stage_status,
    }


def events_for_new_observations(state: dict[str, Any], emitted_steps: set[int]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for observation in state.get("observations") or []:
        step = observation.get("step")
        if step in emitted_steps:
            continue
        if isinstance(step, int):
            emitted_steps.add(step)
        events.extend(_observation_events(observation, state))
    return events


def final_anomaly_event(state: dict[str, Any]) -> dict[str, Any]:
    report = build_anomaly_report(state)
    return _base_event("anomaly_result", "complete", state, "final", report=report, content="异动分析已完成。", status=report["status"], completion_status=report["completion_status"], stop_reason=report["stop_reason"])
