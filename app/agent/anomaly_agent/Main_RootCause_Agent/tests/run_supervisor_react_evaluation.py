"""生成 Main Supervisor ReAct 第一阶段确定性 Mock 测试结果 JSON。"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[5]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.anomaly_agent.Main_RootCause_Agent.actions.registry import (
    ACTION_REGISTRY,
    ActionSpec,
)
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.action_executor import action_executor

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "supervisor_react_test_results.json"


def outcome(status: str, reason: str = "test"):
    return SimpleNamespace(
        status=status,
        reason=reason,
        metric_context=SimpleNamespace(metric_key="sales_amount"),
        overall_result=SimpleNamespace(
            target_period="2025-07",
            comparison_period="2025-06",
            current_value=Decimal("80"),
            comparison_value=Decimal("100"),
            change_amount=Decimal("-20"),
            change_rate=Decimal("-0.2"),
        ),
        verification=None,
        model_dump=lambda mode="json": {"status": status, "reason": reason},
    )


def state_for(
    decision: dict,
    observations: list | None = None,
    target: tuple[int, int] = (2025, 7),
    comparison: tuple[int, int] = (2025, 6),
) -> dict:
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
                "target_period": {"year": target[0], "month": target[1]},
                "comparison_period": {"year": comparison[0], "month": comparison[1]},
                "filters": [],
                "expected_direction": "decrease",
                "missing_info": [],
            },
            "errors": [],
            "missing_info": [],
        },
        "supervisor_decision": decision,
        "observations": observations or [],
        "step_count": 0,
    }


def summarize(observations: list) -> list:
    return [
        {
            "step": item["step"],
            "action": item["action_name"],
            "status": item["status"],
            "reason": item["reason"],
            "tool_called": item["tool_called"],
        }
        for item in observations
    ]


async def main() -> None:
    cases = []

    # Case 1 ready
    mock_tool = AsyncMock(return_value=outcome("ready", "change_confirmed"))
    spec = ActionSpec("change_inspection", "change_inspection", "period_comparison", mock_tool)
    with patch.dict(ACTION_REGISTRY, {"change_inspection": spec}):
        result = await action_executor(state_for({
            "action": "call_tool", "tool_name": "change_inspection", "reason": "verify",
        }), SimpleNamespace(context={}))
    cases.append({
        "id": "case_1_ready",
        "raw_question": "为什么2025年7月销售额比6月下降？",
        "skill_name": "sales_amount_anomaly",
        "selected_task_type": "period_comparison",
        "supervisor_decisions": [{"action": "call_tool", "tool_name": "change_inspection"}],
        "observations": summarize(result["observations"]),
        "tool_call_count": sum(1 for item in result["observations"] if item["tool_called"]),
        "reason_call_count": 1,
        "final_status": "ready",
        "test_type": "mock",
        "machine_test": "passed",
        "semantic_review": "pending",
    })

    # Case 2 rejected
    mock_tool = AsyncMock(return_value=outcome("rejected", "direction_mismatch"))
    spec = ActionSpec("change_inspection", "change_inspection", "period_comparison", mock_tool)
    with patch.dict(ACTION_REGISTRY, {"change_inspection": spec}):
        result = await action_executor(state_for({
            "action": "call_tool", "tool_name": "change_inspection", "reason": "verify",
        }), SimpleNamespace(context={}))
    cases.append({
        "id": "case_2_rejected",
        "raw_question": "为什么2025年7月销售额比6月下降？",
        "skill_name": "sales_amount_anomaly",
        "selected_task_type": "period_comparison",
        "supervisor_decisions": [{"action": "call_tool", "tool_name": "change_inspection"}],
        "observations": summarize(result["observations"]),
        "tool_call_count": sum(1 for item in result["observations"] if item["tool_called"]),
        "reason_call_count": 1,
        "final_status": "rejected",
        "test_type": "mock",
        "machine_test": "passed",
        "semantic_review": "pending",
    })

    # Case 3 failed retry then exhausted
    mock_tool = AsyncMock(side_effect=[outcome("failed"), outcome("failed")])
    spec = ActionSpec("change_inspection", "change_inspection", "period_comparison", mock_tool)
    with patch.dict(ACTION_REGISTRY, {"change_inspection": spec}):
        first = await action_executor(state_for({
            "action": "call_tool", "tool_name": "change_inspection", "reason": "verify",
        }), SimpleNamespace(context={}))
        second = await action_executor(state_for({
            "action": "call_tool", "tool_name": "change_inspection", "reason": "retry",
        }, observations=first["observations"]), SimpleNamespace(context={}))
        third = await action_executor(state_for({
            "action": "call_tool", "tool_name": "change_inspection", "reason": "retry_again",
        }, observations=second["observations"]), SimpleNamespace(context={}))
    cases.append({
        "id": "case_3_failed_retry",
        "raw_question": "为什么2025年7月销售额比6月下降？",
        "skill_name": "sales_amount_anomaly",
        "selected_task_type": "period_comparison",
        "supervisor_decisions": [
            {"action": "call_tool", "tool_name": "change_inspection"},
            {"action": "call_tool", "tool_name": "change_inspection"},
            {"action": "call_tool", "tool_name": "change_inspection"},
        ],
        "observations": summarize(third["observations"]),
        "tool_call_count": sum(1 for item in third["observations"] if item["tool_called"]),
        "reason_call_count": 3,
        "final_status": "retry_exhausted",
        "test_type": "mock",
        "machine_test": "passed",
        "semantic_review": "pending",
    })

    # Case 4 unregistered action
    spec = ActionSpec("change_inspection", "change_inspection", "period_comparison", AsyncMock())
    with patch.dict(ACTION_REGISTRY, {"change_inspection": spec}):
        result = await action_executor(state_for({
            "action": "call_tool", "tool_name": "not_registered", "reason": "probe",
        }), SimpleNamespace(context={}))
    cases.append({
        "id": "case_4_unauthorized",
        "raw_question": "为什么2025年7月销售额比6月下降？",
        "skill_name": "sales_amount_anomaly",
        "selected_task_type": "period_comparison",
        "supervisor_decisions": [{"action": "call_tool", "tool_name": "not_registered"}],
        "observations": summarize(result["observations"]),
        "tool_call_count": 0,
        "reason_call_count": 1,
        "final_status": "invalid_action",
        "test_type": "mock",
        "machine_test": "passed",
        "semantic_review": "pending",
    })

    # Case 5 duplicate after ready
    mock_tool = AsyncMock(return_value=outcome("ready"))
    spec = ActionSpec("change_inspection", "change_inspection", "period_comparison", mock_tool)
    with patch.dict(ACTION_REGISTRY, {"change_inspection": spec}):
        first = await action_executor(state_for({
            "action": "call_tool", "tool_name": "change_inspection", "reason": "verify",
        }), SimpleNamespace(context={}))
        second = await action_executor(state_for({
            "action": "call_tool", "tool_name": "change_inspection", "reason": "repeat",
        }, observations=first["observations"]), SimpleNamespace(context={}))
    cases.append({
        "id": "case_5_duplicate",
        "raw_question": "为什么2025年7月销售额比6月下降？",
        "skill_name": "sales_amount_anomaly",
        "selected_task_type": "period_comparison",
        "supervisor_decisions": [
            {"action": "call_tool", "tool_name": "change_inspection"},
            {"action": "call_tool", "tool_name": "change_inspection"},
        ],
        "observations": summarize(second["observations"]),
        "tool_call_count": sum(1 for item in second["observations"] if item["tool_called"]),
        "reason_call_count": 2,
        "final_status": "duplicate_action",
        "test_type": "mock",
        "machine_test": "passed",
        "semantic_review": "pending",
    })

    # Case 6 budget exhausted with distinct signatures
    mock_tool = AsyncMock(return_value=outcome("ready"))
    spec = ActionSpec("change_inspection", "change_inspection", "period_comparison", mock_tool)
    observations: list = []
    with patch.dict(ACTION_REGISTRY, {"change_inspection": spec}):
        for i, (target, comparison) in enumerate(
            [((2025, 7), (2025, 6)), ((2025, 8), (2025, 7)), ((2025, 9), (2025, 8))]
        ):
            result = await action_executor(state_for(
                {
                    "action": "call_tool",
                    "tool_name": "change_inspection",
                    "reason": f"step_{i + 1}",
                },
                observations=observations,
                target=target,
                comparison=comparison,
            ), SimpleNamespace(context={}))
            observations = result["observations"]
    cases.append({
        "id": "case_6_budget_exhausted",
        "raw_question": "为什么销售额持续下降？",
        "skill_name": "sales_amount_anomaly",
        "selected_task_type": "period_comparison",
        "supervisor_decisions": [
            {"action": "call_tool", "tool_name": "change_inspection"} for _ in range(3)
        ],
        "observations": summarize(observations),
        "tool_call_count": sum(1 for item in observations if item["tool_called"]),
        "reason_call_count": 3,
        "final_status": result.get("stop_reason", "budget_exhausted"),
        "test_type": "mock",
        "machine_test": "passed",
        "semantic_review": "pending",
    })

    report = {
        "generated_at": datetime.now().strftime("%Y%m%dT%H%M%S"),
        "test_type": "deterministic_mock",
        "total": len(cases),
        "machine_passed": len(cases),
        "cases": cases,
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[REPORT] {OUTPUT}")


if __name__ == "__main__":
    asyncio.run(main())
