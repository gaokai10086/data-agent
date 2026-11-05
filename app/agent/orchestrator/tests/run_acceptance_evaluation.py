"""执行应用层阶段验收：30 条真实改写与路由语义用例。"""

from __future__ import annotations

import argparse
import asyncio
import json
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

HERE = Path(__file__).resolve().parent
DEFAULT_CASES = HERE / "acceptance_cases.json"
DEFAULT_OUTPUT_DIR = HERE / "acceptance_results"

EXPECTED_GROUPS = {
    "no_history": 5,
    "cross_business_contamination": 7,
    "multi_turn_followup": 8,
    "ambiguity_boundary": 6,
    "invalid_incomplete": 4,
}


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
    """防止测试图与生产 Orchestrator 的控制流发生漂移。"""
    production = _edge_signature(orchestrator_graph)
    probe = _edge_signature(routing_probe_graph)
    if probe != production:
        raise AssertionError(
            "路由验收图与生产 Orchestrator 拓扑不一致："
            f"missing={production - probe}, extra={probe - production}"
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


def normalize_case(raw: dict[str, Any]) -> dict[str, Any]:
    query = raw["query"]
    session_id = raw["session_id"]
    allowed_intents = raw.get("allowed_intents") or [raw["intent"]]
    intent = allowed_intents[0]
    assertions: dict[str, Any] = {
        "must_contain": raw.get("must_contain", []),
        "must_not_contain": raw.get("must_not_contain", []),
    }
    if raw.get("must_contain_any"):
        assertions["must_contain_any"] = raw["must_contain_any"]
    if raw.get("must_differ_from_input") is not None:
        assertions["must_differ_from_input"] = raw["must_differ_from_input"]
    return {
        "id": raw["id"],
        "group": raw["group"],
        "allowed_intents": allowed_intents,
        "conversation_history": raw.get("history", []),
        "input_state": {
            "query": query,
            "session_id": session_id,
        },
        "rewritten_query_assertions": assertions,
        "expected_output_state": {
            "query": query,
            "session_id": session_id,
            "intent": intent,
            "agent_result": {"entered_agent": intent},
        },
    }


def validate_acceptance_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    cases = [normalize_case(raw) for raw in payload["cases"]]
    if len(cases) != sum(EXPECTED_GROUPS.values()):
        raise ValueError("验收用例总数必须为 30")
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("验收用例 id 必须唯一")
    distribution = Counter(case["group"] for case in cases)
    if dict(distribution) != EXPECTED_GROUPS:
        raise ValueError(
            f"分组数量不符：expected={EXPECTED_GROUPS}, actual={dict(distribution)}"
        )
    return cases


def intent_of(result: dict[str, Any]) -> str | None:
    actual = result["actual_output_state"] or {}
    return actual.get("intent")


def expected_intent_of(result: dict[str, Any]) -> str | None:
    return result["expected_output_state"].get("intent")


def allowed_intents_of(result: dict[str, Any]) -> list[str]:
    return result.get("allowed_intents") or [expected_intent_of(result) or ""]


def intent_accepted(result: dict[str, Any]) -> bool:
    return intent_of(result) in allowed_intents_of(result)


def acceptance_passed(result: dict[str, Any]) -> bool:
    return bool(result["rewrite_assertions"].get("passed")) and intent_accepted(result)


def failure_reasons(result: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if not intent_accepted(result):
        reasons.append(
            f"意图断言失败：允许 {allowed_intents_of(result)}，实际 {intent_of(result)}"
        )
    rewrite = result["rewrite_assertions"]
    if rewrite and not rewrite.get("passed"):
        detail = []
        if rewrite.get("missing_terms"):
            detail.append(f"缺少必要项 {rewrite['missing_terms']}")
        if rewrite.get("missing_alternative_groups"):
            detail.append(f"缺少同义组 {rewrite['missing_alternative_groups']}")
        if rewrite.get("forbidden_terms_present"):
            detail.append(f"出现禁止项 {rewrite['forbidden_terms_present']}")
        reasons.append("改写语义：" + "；".join(detail or ["断言未通过"]))
    return reasons


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    passed = sum(acceptance_passed(result) for result in results)
    intent_correct = sum(
        intent_accepted(result)
        for result in results
    )
    rewrite_passed = sum(
        bool(result["rewrite_assertions"].get("passed"))
        for result in results
    )
    group_metrics: dict[str, Any] = {}
    for group in EXPECTED_GROUPS:
        subset = [result for result in results if result["group"] == group]
        group_metrics[group] = {
            "total": len(subset),
            "passed": sum(acceptance_passed(result) for result in subset),
            "pass_rate": round(
                sum(acceptance_passed(result) for result in subset) / len(subset), 4
            ),
        }
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "case_pass_rate": round(passed / total, 4),
        "intent_accuracy": round(intent_correct / total, 4),
        "rewrite_semantic_pass_rate": round(rewrite_passed / total, 4),
        "history_condition_inheritance_pass_rate": group_metrics[
            "multi_turn_followup"
        ]["pass_rate"],
        "history_contamination_pass_rate": group_metrics[
            "cross_business_contamination"
        ]["pass_rate"],
        "groups": group_metrics,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="运行应用层阶段验收")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--provider", default=DEFAULT_LLM_PROVIDER)
    parser.add_argument("--model-tier", default=DEFAULT_LLM_MODEL)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    payload = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = validate_acceptance_payload(payload)
    if args.limit is not None:
        cases = cases[: args.limit]
    validate_graph_topology()

    results: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        result = await run_case(
            case,
            provider=args.provider,
            model_tier=args.model_tier,
        )
        result["group"] = case["group"]
        result["allowed_intents"] = case["allowed_intents"]
        result["expected_constraints"] = case["rewritten_query_assertions"]
        result["passed"] = acceptance_passed(result)
        result["failure_reasons"] = failure_reasons(result)
        results.append(result)
        if args.verbose:
            status = "PASS" if result["passed"] else "FAIL"
            print(
                f"[{index}/{len(cases)}] {status} {case['id']} "
                f"expected={expected_intent_of(result)} actual={intent_of(result)}"
            )

    failures = [
        {
            "id": result["id"],
            "group": result["group"],
            "original_query": result["input_state"]["query"],
            "conversation_history": result["conversation_history"],
            "actual_rewritten_query": result["actual_rewritten_query"],
            "expected_intent": expected_intent_of(result),
            "allowed_intents": allowed_intents_of(result),
            "actual_intent": intent_of(result),
            "expected_constraints": result["expected_constraints"],
            "failure_reasons": result["failure_reasons"],
        }
        for result in results
        if not result["passed"]
    ]

    report = {
        "evaluated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "dataset": {
            "path": str(args.cases.resolve()),
            "suite_name": payload.get("suite_name"),
            "label_status": payload.get("label_status"),
            "description": payload.get("description", ""),
        },
        "provider": args.provider,
        "model_tier": args.model_tier,
        "summary": summarize(results),
        "failures": failures,
        "results": results,
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    latest_path = args.output_dir / "acceptance_latest.json"
    payload_text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    latest_path.write_text(payload_text, encoding="utf-8")
    summary = report["summary"]
    print(
        f"Case pass={summary['case_pass_rate']:.1%}, "
        f"Intent accuracy={summary['intent_accuracy']:.1%}, "
        f"Rewrite={summary['rewrite_semantic_pass_rate']:.1%}"
    )
    print(f"Latest: {latest_path}")


if __name__ == "__main__":
    asyncio.run(main())
