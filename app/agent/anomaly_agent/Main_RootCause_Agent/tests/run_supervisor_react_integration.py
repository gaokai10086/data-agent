"""真实 LLM 的 Supervisor Reason 集成尝试；真实 Tool 标记 not_run。"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[5]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.supervisor_reason import supervisor_reason

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "supervisor_react_integration_results.json"


def base_state(observations):
    return {
        "raw_question": "为什么2025年7月销售额比6月下降？",
        "extracted_metrics": ["销售额"],
        "skill_result": {
            "status": "matched",
            "skill_name": "sales_amount_anomaly",
            "reason": "指标全部由同一 Skill 支持",
        },
        "schema_result": {
            "status": "matched",
            "task_type": "period_comparison",
            "schema_name": "PeriodComparisonRequest",
            "reason": "双周期比较",
        },
        "parsed_result": {
            "status": "passed",
            "parsed_request": {
                "metric": "销售额",
                "target_period": {"year": 2025, "month": 7},
                "comparison_period": {"year": 2025, "month": 6},
                "filters": [],
                "expected_direction": "decrease",
                "missing_info": [],
            },
            "errors": [],
            "missing_info": [],
        },
        "observations": observations,
        "step_count": 0,
    }


async def main() -> None:
    context = {"llm_provider": "deepseek", "llm_model_tier": "deepseek-v4-flash"}
    result = {"generated_at": datetime.now().strftime("%Y%m%dT%H%M%S")}
    try:
        first = await supervisor_reason(
            base_state([]),
            SimpleNamespace(context=context),
        )
        result["first_reason"] = first["supervisor_decision"]
        ready_observation = [{
            "step": 1,
            "action_name": "change_inspection",
            "status": "ready",
            "reason": "change_confirmed",
            "tool_called": True,
            "dedupe_key": "change_inspection|销售额|2025-07|2025-06|mom|decrease|()",
            "inspection_outcome": None,
        }]
        second = await supervisor_reason(
            base_state(ready_observation),
            SimpleNamespace(context=context),
        )
        result["second_reason"] = second["supervisor_decision"]
        result["tool_execution"] = "not_run"
        result["tool_execution_reason"] = "缺少真实数据库与运行时上下文"
        result["test_type"] = "real_llm"
        result["semantic_review"] = "pending"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["test_type"] = "not_run"
        result["tool_execution"] = "not_run"
        result["semantic_review"] = "pending"

    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[REPORT] {OUTPUT}")


if __name__ == "__main__":
    asyncio.run(main())
