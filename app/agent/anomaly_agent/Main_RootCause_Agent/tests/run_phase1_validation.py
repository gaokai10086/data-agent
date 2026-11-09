"""第一阶段 10 条真实链路验收集。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[5]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.anomaly_agent.Main_RootCause_Agent.graph import main_root_cause_agent
from app.config.app_config import DEFAULT_LLM_MODEL, DEFAULT_LLM_PROVIDER

HERE = Path(__file__).resolve().parent
DEFAULT_CASES = HERE / "phase1_validation_cases.json"
DEFAULT_OUTPUT = HERE / "phase1_validation_results.json"


def _stage_or_skipped(value: dict[str, Any] | None) -> dict[str, Any]:
    if value:
        return value
    return {"status": "skipped"}


def _machine_test(case: dict[str, Any], actual: dict[str, Any]) -> tuple[bool, str | None]:
    expected = case["expected"]
    skill = actual["skill_result"]
    if skill.get("status") != expected["skill_status"]:
        return False, f"skill expected {expected['skill_status']}, got {skill.get('status')}"
    if expected["skill_status"] == "unsupported":
        if actual["schema_result"].get("status") != "skipped":
            return False, "unsupported 后仍执行了 Schema 选择"
        return True, None
    if skill.get("skill_name") != "sales_amount_anomaly":
        return False, f"unexpected skill {skill.get('skill_name')}"

    schema = actual["schema_result"]
    if schema.get("status") != "matched":
        return False, f"schema status {schema.get('status')}"
    if schema.get("task_type") != expected["task_type"]:
        return False, f"task_type expected {expected['task_type']}, got {schema.get('task_type')}"
    if actual["parsed_result"].get("status") != "passed":
        return False, f"parse status {actual['parsed_result'].get('status')}"

    parsed = actual["parsed_result"].get("parsed_request") or {}
    missing = parsed.get("missing_info") or []
    if case["id"] == "sales_location_04":
        target = parsed.get("target_period") or {}
        comparison = parsed.get("comparison_period") or {}
        if (target.get("year"), target.get("month")) != (2025, 7):
            return False, f"anomaly location target_period got {target}"
        if (comparison.get("year"), comparison.get("month")) != (2025, 6):
            return False, f"anomaly location comparison_period got {comparison}"
        if parsed.get("sort_order") != "descending":
            return False, f"anomaly location sort_order got {parsed.get('sort_order')}"
        if any("排名" in str(item) for item in missing):
            return False, "未指定 Top N 不应进入 missing_info"
    if case["id"] == "sales_decomposition_06":
        factors = parsed.get("decomposition_metrics") or []
        if not {"订单量", "客单价"}.issubset(set(factors)):
            return False, f"decomposition_metrics got {factors}"
        relationship = str(parsed.get("relationship") or "")
        if not any(marker in relationship for marker in ("乘", "×", "*")):
            return False, f"relationship got {parsed.get('relationship')}"
    if case["id"] == "sales_hypothesis_07":
        if not parsed.get("hypothesis"):
            return False, "hypothesis 缺失"
        if any("销售额" in str(item) for item in missing):
            return False, "系统可查询的销售额数据不应进入 missing_info"
        if any("促销" in str(item) for item in missing):
            return False, "促销结束时间应作为检索证据，不应进入 missing_info"
    return True, None


async def run_case(case: dict[str, Any], context: dict[str, str]) -> dict[str, Any]:
    state = await main_root_cause_agent.ainvoke(
        {"raw_question": case["question"]},
        context=context,
    )
    skill_result = dict(state.get("skill_result") or {})
    schema_result = dict(state.get("schema_result") or {})
    parsed_result = dict(state.get("parsed_result") or {})
    actual = {
        "skill_result": skill_result or {"status": "skipped"},
        "schema_result": schema_result or {"status": "skipped"},
        "parsed_result": parsed_result or {"status": "skipped"},
    }
    passed, reason = _machine_test(case, actual)
    return {
        "id": case["id"],
        "raw_question": case["question"],
        "extracted_metrics": {"metrics": state.get("extracted_metrics", [])},
        "skill_result": actual["skill_result"],
        "schema_result": actual["schema_result"],
        "parsed_result": actual["parsed_result"],
        "expected_result": case["expected"],
        "machine_test": {"status": "passed" if passed else "failed", "reason": reason},
        "semantic_review": {"status": "pending", "notes": ""},
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--provider", default=DEFAULT_LLM_PROVIDER)
    parser.add_argument("--model-tier", default=DEFAULT_LLM_MODEL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    payload = json.loads(args.cases.read_text(encoding="utf-8"))
    context = {"llm_provider": args.provider, "llm_model_tier": args.model_tier}
    cases = []
    for case in payload["cases"]:
        print(f"[RUN] {case['id']} => {case['question']}")
        cases.append(await run_case(case, context))

    passed = sum(1 for item in cases if item["machine_test"]["status"] == "passed")
    failed = sum(1 for item in cases if item["machine_test"]["status"] == "failed")
    report = {
        "generated_at": datetime.now().strftime("%Y%m%dT%H%M%S"),
        "model": f"{args.provider}/{args.model_tier}",
        "total": len(cases),
        "summary": {"machine_passed": passed, "machine_failed": failed},
        "cases": cases,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[REPORT] {args.output}")
    print(f"[SUMMARY] passed={passed} failed={failed} total={len(cases)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
