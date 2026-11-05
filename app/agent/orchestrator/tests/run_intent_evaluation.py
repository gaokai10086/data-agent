"""逐条运行 Orchestrator 意图评测，并把完整结果写入最新 JSON。

用法：
    python app/agent/orchestrator/tests/run_intent_evaluation.py
    python -m app.agent.orchestrator.tests.run_intent_evaluation --verbose
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from langgraph.constants import END, START
from langgraph.graph import StateGraph

from app.agent.data_agent.context import DataAgentContext
from app.agent.orchestrator.graph import orchestrator_graph, route_intent
from app.agent.orchestrator.nodes.intent_detection import intent_detection
from app.agent.orchestrator.nodes.rewrite_question import rewrite_question
from app.agent.orchestrator.state import OrchestratorState
from app.config.app_config import DEFAULT_LLM_MODEL, DEFAULT_LLM_PROVIDER


INTENTS = ("query_database", "anomaly_analysis", "direct_answer")
HERE = Path(__file__).resolve().parent
DEFAULT_CASES = HERE / "intent_cases.json"
DEFAULT_OUTPUT_DIR = HERE / "results"


def _branch_probe(branch: str):
    async def enter_branch(state: OrchestratorState, runtime) -> dict[str, Any]:
        return {"agent_result": {"entered_agent": branch}}

    return enter_branch


def _build_routing_probe():
    """复用生产路由规则，用探针替换真实子 Agent 内部执行。"""
    builder = StateGraph(OrchestratorState, context_schema=DataAgentContext)
    builder.add_node("rewrite_question", rewrite_question)
    builder.add_node("intent_detection", intent_detection)
    builder.add_node("query_database", _branch_probe("query_database"))
    builder.add_node("anomaly_analysis", _branch_probe("anomaly_analysis"))
    builder.add_node("direct_answer", _branch_probe("direct_answer"))
    builder.add_edge(START, "rewrite_question")
    builder.add_edge("rewrite_question", "intent_detection")
    builder.add_conditional_edges(
        "intent_detection",
        route_intent,
        {
            "query_database": "query_database",
            "anomaly_analysis": "anomaly_analysis",
            "direct_answer": "direct_answer",
        },
    )
    builder.add_edge("query_database", END)
    builder.add_edge("anomaly_analysis", END)
    builder.add_edge("direct_answer", END)
    return builder.compile()


routing_probe_graph = _build_routing_probe()


def _edge_signature(graph) -> set[tuple[str, str, bool]]:
    return {
        (edge.source, edge.target, edge.conditional)
        for edge in graph.get_graph().edges
    }


def validate_graph_topology() -> None:
    """防止探针图与生产 Orchestrator 的控制流发生漂移。"""
    production = _edge_signature(orchestrator_graph)
    probe = _edge_signature(routing_probe_graph)
    if probe != production:
        raise AssertionError(
            "路由评测图与生产 Orchestrator 拓扑不一致："
            f"missing={production - probe}, extra={probe - production}"
        )


def _percentile(values: list[float], percentile: float) -> float:
    """使用最近秩法计算百分位，避免依赖额外统计库。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _safe_rate(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _expected_intent(result: dict[str, Any]) -> str | None:
    return result["expected_output_state"].get("intent")


def _actual_intent(result: dict[str, Any]) -> str | None:
    actual_state = result["actual_output_state"] or {}
    return actual_state.get("intent")


def validate_cases(cases: list[dict[str, Any]]) -> None:
    """尽早拒绝不符合 State 输入/输出契约的用例。"""
    ids = [case.get("id") for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("测试用例 id 必须唯一")

    for case in cases:
        case_id = case.get("id", "<missing-id>")
        input_state = case.get("input_state")
        expected_state = case.get("expected_output_state")
        if not isinstance(input_state, dict) or not input_state:
            raise ValueError(f"{case_id}: input_state 必须是非空对象")
        if not input_state.get("session_id"):
            raise ValueError(f"{case_id}: input_state.session_id 不能为空")
        if not isinstance(expected_state, dict) or not expected_state:
            raise ValueError(f"{case_id}: expected_output_state 必须是非空对象")
        expected_intent = expected_state.get("intent")
        if expected_intent not in INTENTS:
            raise ValueError(f"{case_id}: expected_output_state.intent 非法")
        agent_result = expected_state.get("agent_result")
        if not isinstance(agent_result, dict):
            raise ValueError(f"{case_id}: expected_output_state.agent_result 缺失")
        if agent_result.get("entered_agent") != expected_intent:
            raise ValueError(
                f"{case_id}: agent_result.entered_agent 必须与 expected intent 一致"
            )
        history = case.get("conversation_history")
        if (
            not isinstance(history, list)
            or not history
            or not all(isinstance(item, str) and item for item in history)
        ):
            raise ValueError(f"{case_id}: conversation_history 必须是非空字符串列表")
        assertions = case.get("rewritten_query_assertions")
        if not isinstance(assertions, dict) or not assertions:
            raise ValueError(f"{case_id}: rewritten_query_assertions 必须是非空对象")
        alternatives = assertions.get("must_contain_any", [])
        if not isinstance(alternatives, list) or not all(
            isinstance(group, list)
            and group
            and all(isinstance(term, str) and term for term in group)
            for group in alternatives
        ):
            raise ValueError(
                f"{case_id}: must_contain_any 必须是非空字符串二维列表"
            )
        for key, value in input_state.items():
            if key not in expected_state or expected_state[key] != value:
                raise ValueError(
                    f"{case_id}: expected_output_state 未保留输入字段 {key}"
                )


def validate_dataset(payload: dict[str, Any]) -> None:
    cases = payload["cases"]
    target = payload.get("target")
    if not isinstance(target, dict):
        raise ValueError("数据集缺少 target 配置")
    if target.get("total") != len(cases):
        raise ValueError(
            f"用例总数应为 {target.get('total')}，实际为 {len(cases)}"
        )
    expected_distribution = target.get("per_intent", {})
    actual_distribution = Counter(
        case["expected_output_state"]["intent"]
        for case in cases
    )
    if dict(actual_distribution) != expected_distribution:
        raise ValueError(
            "意图分布不符合 target："
            f"expected={expected_distribution}, actual={dict(actual_distribution)}"
        )


def _state_matches_expected(
    actual_state: dict[str, Any] | None,
    expected_state: dict[str, Any],
) -> bool:
    if not isinstance(actual_state, dict):
        return False
    return all(
        key in actual_state and actual_state[key] == value
        for key, value in expected_state.items()
    )


def _check_rewritten_query(
    case: dict[str, Any],
    actual_state: dict[str, Any] | None,
) -> tuple[bool, dict[str, Any]]:
    assertions = case.get("rewritten_query_assertions")
    if not assertions:
        return True, {}

    rewritten = str((actual_state or {}).get("rewritten_query", ""))
    must_contain = assertions.get("must_contain", [])
    must_contain_any = assertions.get("must_contain_any", [])
    must_not_contain = assertions.get("must_not_contain", [])
    missing = [term for term in must_contain if term not in rewritten]
    missing_alternatives = [
        alternatives
        for alternatives in must_contain_any
        if not any(term in rewritten for term in alternatives)
    ]
    forbidden = [term for term in must_not_contain if term in rewritten]
    differs = (
        rewritten.strip() != str(case["input_state"].get("query", "")).strip()
        if assertions.get("must_differ_from_input")
        else True
    )
    passed = not missing and not missing_alternatives and not forbidden and differs
    return passed, {
        "actual_rewritten_query": rewritten,
        "missing_terms": missing,
        "missing_alternative_groups": missing_alternatives,
        "forbidden_terms_present": forbidden,
        "differs_from_input": rewritten.strip()
        != str(case["input_state"].get("query", "")).strip(),
        "passed": passed,
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    passed = sum(result["passed"] for result in results)
    classification_passed = sum(
        _expected_intent(result) == _actual_intent(result)
        for result in results
    )
    latencies = [float(result["latency_ms"]) for result in results]

    per_intent: dict[str, dict[str, Any]] = {}
    f1_scores: list[float] = []
    for intent in INTENTS:
        true_positive = sum(
            _expected_intent(result) == intent and _actual_intent(result) == intent
            for result in results
        )
        false_positive = sum(
            _expected_intent(result) != intent and _actual_intent(result) == intent
            for result in results
        )
        false_negative = sum(
            _expected_intent(result) == intent and _actual_intent(result) != intent
            for result in results
        )
        precision = _safe_rate(true_positive, true_positive + false_positive)
        recall = _safe_rate(true_positive, true_positive + false_negative)
        f1 = _safe_rate(2 * precision * recall, precision + recall)
        f1_scores.append(f1)
        per_intent[intent] = {
            "support": sum(_expected_intent(result) == intent for result in results),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        }

    confusion: dict[str, Counter[str]] = {intent: Counter() for intent in INTENTS}
    for result in results:
        expected = _expected_intent(result)
        actual = _actual_intent(result) or "<error>"
        if expected in confusion:
            confusion[expected][actual] += 1

    rewrite_results = [
        result["rewrite_assertions"]["passed"]
        for result in results
        if result["rewrite_assertions"]
    ]
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "case_pass_rate": round(_safe_rate(passed, total), 4),
        "classification_accuracy": round(
            _safe_rate(classification_passed, total), 4
        ),
        "macro_f1": round(sum(f1_scores) / len(f1_scores), 4),
        "rewrite_assertions": {
            "total": len(rewrite_results),
            "passed": sum(rewrite_results),
            "pass_rate": round(
                _safe_rate(sum(rewrite_results), len(rewrite_results)), 4
            ),
        },
        "per_intent": per_intent,
        "confusion_matrix": {
            expected: dict(counts)
            for expected, counts in confusion.items()
        },
        "latency_ms": {
            "p50": round(_percentile(latencies, 0.50), 2),
            "p95": round(_percentile(latencies, 0.95), 2),
            "max": round(max(latencies, default=0.0), 2),
        },
    }


async def run_case(
    case: dict[str, Any],
    *,
    provider: str,
    model_tier: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    actual_output_state: dict[str, Any] | None = None
    error: str | None = None
    try:
        history = case.get("conversation_history", [])
        with patch(
            "app.agent.orchestrator.nodes.rewrite_question.conversation_history.recent",
            return_value=history,
        ):
            actual_output_state = await routing_probe_graph.ainvoke(
                input=dict(case["input_state"]),
                context={
                    "llm_provider": provider,
                    "llm_model_tier": model_tier,
                },
            )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    latency_ms = (time.perf_counter() - started) * 1000
    rewrite_passed, rewrite_details = _check_rewritten_query(case, actual_output_state)
    state_passed = _state_matches_expected(
        actual_output_state,
        case["expected_output_state"],
    )

    return {
        "id": case["id"],
        "category": case.get("category", ""),
        "conversation_history": case.get("conversation_history", []),
        "input_state": case["input_state"],
        "expected_output_state": case["expected_output_state"],
        "actual_rewritten_query": (actual_output_state or {}).get("rewritten_query"),
        "actual_output_state": actual_output_state,
        "state_assertions_passed": state_passed,
        "rewrite_assertions": rewrite_details,
        "passed": state_passed and rewrite_passed,
        "latency_ms": round(latency_ms, 2),
        "error": error,
    }


async def evaluate(
    cases: list[dict[str, Any]],
    *,
    dataset_metadata: dict[str, Any],
    provider: str,
    model_tier: str,
    verbose: bool,
) -> dict[str, Any]:
    started_at = datetime.now().astimezone()
    started = time.perf_counter()
    results: list[dict[str, Any]] = []

    for index, case in enumerate(cases, start=1):
        result = await run_case(case, provider=provider, model_tier=model_tier)
        results.append(result)
        if verbose:
            status = "PASS" if result["passed"] else "FAIL"
            expected = _expected_intent(result)
            actual = _actual_intent(result)
            print(
                f"[{index}/{len(cases)}] {status} {case['id']}: "
                f"expected={expected} actual={actual} "
                f"latency={result['latency_ms']:.0f}ms"
            )

    summary = summarize(results)
    return {
        "evaluated_at": started_at.isoformat(timespec="seconds"),
        "dataset": dataset_metadata,
        "provider": provider,
        "model_tier": model_tier,
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        "summary": summary,
        "results": results,
    }


def write_report(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")
    history_path = output_dir / f"intent_eval_{timestamp}.json"
    latest_path = output_dir / "intent_latest.json"
    compact_results = [
        {
            "session_id": result["input_state"].get("session_id"),
            "previous_conversation": result["conversation_history"],
            "current_query": result["input_state"].get("query"),
            "actual_rewritten_query": result["actual_rewritten_query"],
            "rewritten_intent": (result["actual_output_state"] or {}).get("intent"),
            "passed": result["passed"],
            "latency_ms": result["latency_ms"],
        }
        for result in report["results"]
    ]
    payload = json.dumps(
        {"results": compact_results},
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    history_path.write_text(payload, encoding="utf-8")
    latest_path.write_text(payload, encoding="utf-8")
    return history_path, latest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行 Orchestrator 意图评测")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--provider", default=DEFAULT_LLM_PROVIDER)
    parser.add_argument("--model-tier", default=DEFAULT_LLM_MODEL)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    payload = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = payload["cases"]
    validate_dataset(payload)
    if args.limit is not None:
        cases = cases[: args.limit]
    validate_graph_topology()
    validate_cases(cases)

    report = await evaluate(
        cases,
        dataset_metadata={
            "path": str(args.cases.resolve()),
            "version": payload.get("version"),
            "suite_name": payload.get("suite_name"),
            "label_status": payload.get("label_status"),
            "description": payload.get("description", ""),
            "case_schema": {
                "input_state": "OrchestratorState entering the routing graph",
                "actual_output_state": "Final OrchestratorState returned by the routing graph",
                "expected_output_state": "Expected State fields including the selected branch",
                "conversation_history": "Injected history used by the real rewrite node",
                "rewritten_query_assertions": "Semantic constraints for LLM-generated rewrite text",
                "branch_execution": (
                    "Uses production route_intent and graph topology; child Agent "
                    "internals are replaced by probe nodes that record entered_agent"
                ),
            },
        },
        provider=args.provider,
        model_tier=args.model_tier,
        verbose=args.verbose,
    )
    history_path, latest_path = write_report(report, args.output_dir)
    summary = report["summary"]
    print(
        f"Case pass={summary['case_pass_rate']:.1%}, "
        f"Intent accuracy={summary['classification_accuracy']:.1%}, "
        f"Macro-F1={summary['macro_f1']:.4f}, "
        f"P95={summary['latency_ms']['p95']:.0f}ms"
    )
    print(f"History: {history_path}")
    print(f"Latest:  {latest_path}")


if __name__ == "__main__":
    asyncio.run(main())
