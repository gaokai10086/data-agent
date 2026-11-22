"""Aggregate business data queries once per Action Observation."""
from __future__ import annotations


def observation_sql_count(item: dict) -> int:
    action = item.get("action_name")
    if action == "change_inspection":
        return 2 if (item.get("inspection_outcome") or {}).get("overall_result") else 0
    if action == "dimension_attribution":
        return int((item.get("attribution_result") or {}).get("total_query_count") or (item.get("evidence") or {}).get("total_query_count") or 0)
    if action == "metric_decomposition":
        result = item.get("decomposition_result") or {}
        return sum(len(((row.get("values") or {}).get("sql_by_period") or {})) for row in result.get("observations") or [])
    if action == "scoped_metric_decomposition":
        return int((item.get("evidence") or {}).get("total_query_count") or 0)
    return 0


def sql_usage(observations: list[dict]) -> dict[str, object]:
    by_action: dict[str, int] = {}
    for item in observations:
        name = str(item.get("action_name") or "unknown")
        by_action[name] = by_action.get(name, 0) + observation_sql_count(item)
    return {"total": sum(by_action.values()), "by_action": by_action}
