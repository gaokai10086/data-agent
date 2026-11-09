"""Skill 注册、匹配与加载阶段真实 LLM 评测。"""

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
from app.agent.anomaly_agent.Main_RootCause_Agent.skills.registry import (
    load_skill,
    resolve_skill_capabilities,
)
from app.config.app_config import DEFAULT_LLM_MODEL, DEFAULT_LLM_PROVIDER

HERE = Path(__file__).resolve().parent
DEFAULT_CASES = HERE / "skill_pipeline_cases.json"
DEFAULT_OUTPUT = HERE / "skill_pipeline_results.json"


def _skill_summary(skill_name: str | None) -> dict[str, Any] | None:
    if not skill_name:
        return None
    skill = load_skill(skill_name)
    capabilities = resolve_skill_capabilities(skill)
    return {
        "name": skill.name,
        "version": skill.version,
        "description": skill.description,
        "metrics": list(skill.supported_metrics),
        "related_metrics": list(skill.related_metrics),
        "business_rules": list(skill.business_rules),
        "analysis_guidance": skill.analysis_guidance,
        "allowed_capabilities": capabilities["allowed_capabilities"],
        "available_capabilities": capabilities["available_capabilities"],
    }


async def run_case(case: dict[str, Any], context: dict[str, str]) -> dict[str, Any]:
    result = await main_root_cause_agent.ainvoke(
        {"raw_question": case["question"]},
        context=context,
    )
    record: dict[str, Any] = {
        "id": case["id"],
        "raw_question": case["question"],
        "extracted_metrics": result.get("extracted_metrics", []),
        "skill_match_status": result.get("skill_match_status"),
        "selected_skill_name": result.get("selected_skill_name"),
        "skill_match_reason": result.get("skill_match_reason"),
        "loaded_skill": _skill_summary(result.get("selected_skill_name")),
        "allowed_capabilities": (
            _skill_summary(result.get("selected_skill_name")).get("allowed_capabilities", [])
            if result.get("selected_skill_name")
            else []
        ),
        "available_capabilities": (
            _skill_summary(result.get("selected_skill_name")).get("available_capabilities", [])
            if result.get("selected_skill_name")
            else []
        ),
        "machine_test": result.get("skill_match_status") == case["expected_status"],
        "semantic_status": "pending",
    }
    return record


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
        results.append(await run_case(case, context))

    report = {
        "generated_at": datetime.now().strftime("%Y%m%dT%H%M%S"),
        "provider": args.provider,
        "model_tier": args.model_tier,
        "total": len(results),
        "machine_passed": sum(1 for item in results if item["machine_test"]),
        "cases": results,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[REPORT] {args.output}")
    print(
        f"[SUMMARY] machine_passed={report['machine_passed']} total={report['total']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
