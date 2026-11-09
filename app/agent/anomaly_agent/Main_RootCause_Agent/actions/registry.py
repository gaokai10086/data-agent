"""最小 Action 注册表。

Action 名称是受控枚举，不能由 LLM 自由拼写；执行能力必须先注册。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from app.agent.anomaly_agent.common.change_inspection.tool import verify_metric_change
from app.agent.anomaly_agent.schemas import AnalysisRequest, InspectionOutcome


ActionExecutor = Callable[
    [AnalysisRequest, Any],
    Awaitable[InspectionOutcome],
]

# 最多允许执行 3 次 Action；Reason 次数由 Action 结果驱动。
MAX_ACTION_EXECUTIONS = 3


@dataclass(frozen=True)
class ActionSpec:
    name: str
    capability: str
    task_type: str
    executor: ActionExecutor


ACTION_REGISTRY: dict[str, ActionSpec] = {
    "change_inspection": ActionSpec(
        name="change_inspection",
        capability="change_inspection",
        task_type="period_comparison",
        executor=verify_metric_change,
    ),
}


def get_action(name: str) -> ActionSpec:
    action = ACTION_REGISTRY.get(name)
    if action is None:
        raise ValueError(f"未注册 Action：{name}")
    return action
