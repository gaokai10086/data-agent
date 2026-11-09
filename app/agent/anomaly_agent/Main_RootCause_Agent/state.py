"""Main_RootCause_Agent 的状态契约。"""

from typing import Literal, TypedDict


class SkillResult(TypedDict, total=False):
    status: Literal["matched", "unsupported", "ambiguous", "missing_information"]
    skill_name: str | None
    reason: str


class SchemaResult(TypedDict, total=False):
    status: Literal["matched", "unsupported", "ambiguous", "unsupported_multi_task"]
    task_type: str | None
    schema_name: str | None
    reason: str


class ParsedResult(TypedDict, total=False):
    status: Literal["passed", "failed"]
    parsed_request: dict | None
    errors: list[str]
    missing_info: list[str]


class SupervisorDecision(TypedDict, total=False):
    action: Literal["call_tool", "finish", "stop"]
    tool_name: str | None
    reason: str


class Observation(TypedDict, total=False):
    step: int
    action_name: str
    status: Literal["ready", "rejected", "failed", "blocked"]
    reason: str
    evidence: dict | None
    error: str | None
    tool_called: bool
    dedupe_key: str
    inspection_outcome: dict | None


class RootCauseState(TypedDict, total=False):
    raw_question: str
    extracted_metrics: list[str]
    skill_result: SkillResult
    schema_result: SchemaResult
    parsed_result: ParsedResult
    supervisor_decision: SupervisorDecision
    observations: list[Observation]
    step_count: int
    stop_reason: str
