"""Main_RootCause_Agent 第一阶段真实 LLM 流程评测。

从 llm_flow_cases.json 读取问题，按 Graph 节点顺序真实调用
receive_question → parse_request → select_skill，逐步记录 State，
并把原始问题、解析请求、选中 Skill 与失败原因写入固定结果文件。

运行方式：
    .venv/bin/python app/agent/anomaly_agent/Main_RootCause_Agent/tests/run_llm_flow_evaluation.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[5]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.parse_request import parse_request
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.receive_question import receive_question
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.select_skill import select_skill
from app.agent.anomaly_agent.schemas import AnalysisRequest
from app.config.app_config import DEFAULT_LLM_MODEL, DEFAULT_LLM_PROVIDER

HERE = Path(__file__).resolve().parent
DEFAULT_CASES = HERE / "llm_flow_cases.json"
DEFAULT_OUTPUT = HERE / "llm_flow_results.json"


def _filters_signature(filters: list[dict[str, Any]]) -> frozenset[tuple[Any, ...]]:
    return frozenset(
        (item.get("dimension"), item.get("value"), item.get("operator", "="))
        for item in filters
    )


_FAILURE_KEYWORDS = {
    "blank_question": "问题不能为空",
    "invalid_month": "无效月份",
    "same_period": "目标月份与对比月份不能相同",
    "unsupported_skill": "没有 Skill 可以处理指标",
}


def _failure_matches(expected_failure: str, exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}"
    if expected_failure == "missing_comparison":
        lowered = text.lower()
        if any(marker in lowered for marker in ("connection", "timeout", "api key", "401", "403")):
            return False
        return True
    keyword = _FAILURE_KEYWORDS.get(expected_failure)
    return keyword is not None and keyword in text


def _check_case(case: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    request = actual.get("analysis_request")
    expected = case["expected"]
    problems: list[str] = []

    if not isinstance(request, AnalysisRequest):
        problems.append("analysis_request 缺失或类型错误")
        return problems

    checks = {
        "metric": request.metric,
        "target_period": request.target_period,
        "comparison_period": request.comparison_period,
        "comparison_type": request.comparison_type,
        "expected_direction": request.expected_direction,
    }
    for field, actual_value in checks.items():
        if actual_value != expected.get(field):
            problems.append(f"{field}: 期望 {expected.get(field)!r}，实际 {actual_value!r}")

    actual_filters = _filters_signature([item.model_dump() for item in request.filters])
    expected_filters = _filters_signature(expected.get("filters", []))
    if actual_filters != expected_filters:
        problems.append(
            f"filters: 期望 {sorted(expected_filters)}，实际 {sorted(actual_filters)}"
        )

    actual_skill = actual.get("selected_skill_name")
    if actual_skill != expected.get("selected_skill_name"):
        problems.append(
            "selected_skill_name: "
            f"期望 {expected.get('selected_skill_name')!r}，实际 {actual_skill!r}"
        )

    return problems


async def _run_case(
    case: dict[str, Any],
    context: dict[str, str],
) -> tuple[dict[str, Any], Exception | None]:
    """按 Graph 节点顺序逐步执行，保留每一步成功写入的 State。"""
    state: dict[str, Any] = {"raw_question": case["question"]}
    runtime = SimpleNamespace(context=context)

    try:
        receive_question({"raw_question": case["question"]})
    except Exception as exc:
        state["error_stage"] = "receive_question"
        state["error"] = f"{type(exc).__name__}: {exc}"
        return state, exc

    try:
        parsed = await parse_request(
            {"raw_question": case["question"]},
            runtime,
        )
        state.update(parsed)
    except Exception as exc:
        state["error_stage"] = "parse_request"
        state["error"] = f"{type(exc).__name__}: {exc}"
        return state, exc

    try:
        selected = select_skill({"analysis_request": state["analysis_request"]})
        state.update(selected)
    except Exception as exc:
        state["error_stage"] = "select_skill"
        state["error"] = f"{type(exc).__name__}: {exc}"
        return state, exc

    return state, None


def _serialize_result(result: dict[str, Any]) -> dict[str, Any]:
    serialized = dict(result)
    serialized.setdefault("raw_question", None)
    serialized.setdefault("analysis_request", None)
    serialized.setdefault("selected_skill_name", None)
    request = serialized.get("analysis_request")
    if isinstance(request, AnalysisRequest):
        serialized["analysis_request"] = request.model_dump()
    return serialized


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--provider", default=DEFAULT_LLM_PROVIDER)
    parser.add_argument("--model-tier", default=DEFAULT_LLM_MODEL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    payload = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = payload["cases"]
    context = {
        "llm_provider": args.provider,
        "llm_model_tier": args.model_tier,
    }

    results = []
    failures = []
    for case in cases:
        expected_failure = case.get("expected_failure")
        print(f"[RUN] {case['id']} => {case['question']}")
        actual, exc = await _run_case(case, context)
        if exc is not None:
            if expected_failure and _failure_matches(expected_failure, exc):
                print(f"[PASS] {case['id']}（预期失败：{expected_failure}）")
                results.append(
                    {
                        "case_id": case["id"],
                        "passed": True,
                        "problems": [],
                        "expected_failure": expected_failure,
                        "actual": _serialize_result(actual),
                    }
                )
            else:
                reason = (
                    f"失败类型不匹配，预期 {expected_failure}"
                    if expected_failure
                    else "执行失败"
                )
                failures.append((case["id"], f"{reason}: {exc}"))
                print(f"[FAIL] {case['id']} {reason}: {exc}")
                results.append(
                    {
                        "case_id": case["id"],
                        "passed": False,
                        "problems": [f"{reason}: {exc}"],
                        "expected_failure": expected_failure,
                        "actual": _serialize_result(actual),
                    }
                )
            continue

        if expected_failure:
            problems = ["期望失败，但流程成功产出了结果"]
            failures.append((case["id"], problems[0]))
            print(f"[FAIL] {case['id']}: {problems[0]}")
            results.append(
                {
                    "case_id": case["id"],
                    "passed": False,
                    "problems": problems,
                    "expected_failure": expected_failure,
                    "actual": _serialize_result(actual),
                }
            )
            continue

        problems = _check_case(case, actual)
        passed = not problems
        if passed:
            print(f"[PASS] {case['id']}")
        else:
            failures.append((case["id"], "; ".join(problems)))
            print(f"[FAIL] {case['id']}: {'; '.join(problems)}")

        results.append(
            {
                "case_id": case["id"],
                "passed": passed,
                "problems": problems,
                "actual": _serialize_result(actual),
            }
        )

    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    report = {
        "generated_at": stamp,
        "provider": args.provider,
        "model_tier": args.model_tier,
        "total": len(cases),
        "passed": len(cases) - len(failures),
        "failed": len(failures),
        "cases": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n[REPORT] {args.output}")
    print(f"[SUMMARY] passed={report['passed']} failed={report['failed']} total={report['total']}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
