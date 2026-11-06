"""统一 TaskRequest 解析的真实 LLM 评测。

每个用例保存：原始问题、TaskRequest JSON、Skill 匹配结果、AnalysisRequest
转换结果或错误。机器校验只判断类型和确定性规则，语义正确性由人工复核。
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

from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.parse_task import parse_task
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.select_skill import select_skill
from app.agent.anomaly_agent.Main_RootCause_Agent.task_request_conversion import (
    task_request_to_analysis_request,
)
from app.agent.anomaly_agent.schemas import TaskRequest
from app.config.app_config import DEFAULT_LLM_MODEL, DEFAULT_LLM_PROVIDER

HERE = Path(__file__).resolve().parent
DEFAULT_CASES = HERE / "task_parse_cases.json"
DEFAULT_OUTPUT = HERE / "task_parse_results.json"


def _serialize_task(task: TaskRequest) -> dict[str, Any]:
    return task.model_dump(mode="json")


async def _run_case(case: dict[str, Any], context: dict[str, str]) -> dict[str, Any]:
    raw_question = case["question"]
    runtime = SimpleNamespace(context=context, stream_writer=lambda _: None)
    result: dict[str, Any] = {"id": case["id"], "raw_question": raw_question}
    try:
        parsed = await parse_task({"raw_question": raw_question}, runtime)
        task = parsed["task_request"]
        result["task_request"] = _serialize_task(task)
        try:
            selected = select_skill({"task_request": task})
            result["skill"] = {"matched": True, "name": selected["selected_skill_name"]}
        except Exception as exc:
            result["skill"] = {"matched": False, "error": f"{type(exc).__name__}: {exc}"}
        try:
            request = task_request_to_analysis_request(task)
            result["analysis_request"] = request.model_dump(mode="json")
            result["conversion_error"] = None
        except Exception as exc:
            result["analysis_request"] = None
            result["conversion_error"] = f"{type(exc).__name__}: {exc}"
        result["parse_error"] = None
    except Exception as exc:
        result["task_request"] = None
        result["skill"] = None
        result["analysis_request"] = None
        result["conversion_error"] = None
        result["parse_error"] = f"{type(exc).__name__}: {exc}"
    return result


def _machine_passed(case: dict[str, Any], result: dict[str, Any]) -> bool:
    check = case["expected_check"]
    if check == "parse_error":
        return result["parse_error"] is not None and result["task_request"] is None
    if check == "conversion_error":
        return (
            result["parse_error"] is None
            and result["task_request"] is not None
            and result["conversion_error"] is not None
        )
    return result["parse_error"] is None and result["task_request"] is not None


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--provider", default=DEFAULT_LLM_PROVIDER)
    parser.add_argument("--model-tier", default=DEFAULT_LLM_MODEL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    payload = json.loads(args.cases.read_text(encoding="utf-8"))
    context = {"llm_provider": args.provider, "llm_model_tier": args.model_tier}

    results = []
    for case in payload["cases"]:
        print(f"[RUN] {case['id']} => {case['question']}")
        result = await _run_case(case, context)
        result["machine_passed"] = _machine_passed(case, result)
        results.append(result)
        print(f"[{'PASS' if result['machine_passed'] else 'CHECK'}] {case['id']}")

    report = {
        "generated_at": datetime.now().strftime("%Y%m%dT%H%M%S"),
        "provider": args.provider,
        "model_tier": args.model_tier,
        "total": len(results),
        "machine_passed": sum(1 for item in results if item["machine_passed"]),
        "cases": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n[REPORT] {args.output}")
    print(
        f"[SUMMARY] machine_passed={report['machine_passed']} total={report['total']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
