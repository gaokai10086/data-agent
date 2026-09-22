"""评测指标计算。"""
from statistics import mean


def percentile(values: list[float], percent: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * (percent / 100.0)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return float(ordered[lower])
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower))


def summarize(results: list) -> dict:
    total = len(results)
    passed = sum(1 for result in results if result.passed)

    by_category: dict[str, dict] = {}
    for result in results:
        stats = by_category.setdefault(result.category, {"passed": 0, "total": 0})
        stats["total"] += 1
        if result.passed:
            stats["passed"] += 1
    for stats in by_category.values():
        stats["accuracy"] = stats["passed"] / stats["total"] if stats["total"] else 0.0

    latencies = [result.latency_ms for result in results]
    return {
        "total": total,
        "passed": passed,
        "execution_accuracy": passed / total if total else 0.0,
        "by_category": by_category,
        "latency_p50": percentile(latencies, 50),
        "latency_p95": percentile(latencies, 95),
        "avg_schema_linking_recall": (
            mean([result.schema_linking_recall for result in results]) if results else 0.0
        ),
        "avg_join_accuracy": (
            mean([result.join_accuracy for result in results]) if results else 0.0
        ),
    }
