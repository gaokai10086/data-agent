"""One-call, evidence-bound narrative generation for the final anomaly report."""

from __future__ import annotations

import json
import re
import time
from decimal import Decimal, InvalidOperation
from typing import Any

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field, ConfigDict

from app.core.llm import get_context_llm
from app.prompt.prompt_loader import load_prompt


class NarrativeFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_id: str
    kind: str
    payload: dict[str, Any]


class NarrativeContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    overall: NarrativeFact | None = None
    decomposition: NarrativeFact | None = None
    drilldowns: list[NarrativeFact] = Field(default_factory=list)
    diagnosis: NarrativeFact | None = None


class Insight(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    evidence_ids: list[str]


class DrilldownInsight(Insight):
    task_id: str


class NarrativeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    overview: Insight
    decomposition: Insight | None = None
    drilldowns: list[DrilldownInsight] = Field(default_factory=list)
    diagnosis: Insight | None = None


def context_from_report(report: dict[str, Any]) -> NarrativeContext:
    overall = report.get("overall") or {}
    decomposition = report.get("decomposition_section") or {}
    sections = report.get("drilldown_sections") or []
    diagnosis = report.get("diagnosis_section") or {}
    return NarrativeContext(
        overall=NarrativeFact(evidence_id="overall", kind="overall", payload=overall) if overall else None,
        decomposition=NarrativeFact(evidence_id="decomposition", kind="decomposition", payload={
            "components": decomposition.get("components"), "reconciliation": decomposition.get("reconciliation")
        }) if decomposition.get("reconciliation", {}).get("matches") is True else None,
        drilldowns=[NarrativeFact(evidence_id=f"path:{s['task_id']}", kind="drilldown", payload={
            "task_id": s["task_id"], "depth": s.get("depth"), "dimension_name": s.get("dimension_name"),
            "selected_member": s.get("selected_member"), "parent_scope": s.get("parent_scope"),
            "rows": s.get("rows"), "reconciliation": s.get("reconciliation"),
        }) for s in sections if s.get("task_id") and s.get("selected_member") and all(
            (s.get("reconciliation") or {}).get(k) is True for k in ("comparison_matches", "current_matches", "change_matches")
        )],
        diagnosis=NarrativeFact(evidence_id="diagnosis", kind="diagnosis", payload={
            "findings": diagnosis.get("findings"), "hypotheses": diagnosis.get("hypotheses")
        }) if diagnosis else None,
    )


def _validate(output: NarrativeOutput, context: NarrativeContext) -> None:
    allowed = {fact.evidence_id for fact in [context.overall, context.decomposition, *context.drilldowns, context.diagnosis] if fact}
    expected = {fact.payload["task_id"] for fact in context.drilldowns}
    if {item.task_id for item in output.drilldowns} != expected or len(output.drilldowns) != len(expected):
        raise ValueError("drilldown task IDs differ from verified paths")
    if bool(output.decomposition) != bool(context.decomposition) or bool(output.diagnosis) != bool(context.diagnosis):
        raise ValueError("narrative sections differ from verified evidence")
    insights = [output.overview, *output.drilldowns]
    if output.decomposition:
        insights.append(output.decomposition)
    if output.diagnosis:
        insights.append(output.diagnosis)
    def approved_numbers(value: Any, key: str = "") -> set[Decimal]:
        approved: set[Decimal] = set()
        if isinstance(value, dict):
            for child_key, item in value.items():
                if child_key in {"sql", "sql_evidence", "sql_by_period"}:
                    continue
                approved.update(approved_numbers(item, child_key))
        elif isinstance(value, list):
            for item in value:
                approved.update(approved_numbers(item, key))
        elif isinstance(value, (str, int, float, Decimal)) and not isinstance(value, bool):
            try:
                number = Decimal(str(value).replace(",", ""))
            except InvalidOperation:
                return approved
            if number.is_finite():
                approved.add(number)
                approved.add(abs(number))
                approved.add(number.quantize(Decimal("0.01")))
                approved.add(abs(number).quantize(Decimal("0.01")))
                if "rate" in key or "贡献率" in key or "变化率" in key:
                    approved.add((number * 100).quantize(Decimal("0.01")))
                    approved.add(abs((number * 100).quantize(Decimal("0.01"))))
        return approved

    number_pattern = re.compile(r"(?<![\w])[-+]?\d[\d,]*(?:\.\d+)?%?")
    for insight in insights:
        if not insight.text.strip() or not insight.evidence_ids or not set(insight.evidence_ids) <= allowed:
            raise ValueError("invalid evidence references")
        if re.search(r"\b(?:select|from|where)\b", insight.text, re.I):
            raise ValueError("model generated SQL")
        cited_facts = [fact.payload for fact in [context.overall, context.decomposition, *context.drilldowns, context.diagnosis]
                       if fact and fact.evidence_id in insight.evidence_ids]
        valid_numbers = approved_numbers(cited_facts)
        text_without_periods = insight.text
        for fact in cited_facts:
            for period_key in ("target_period", "comparison_period"):
                period = fact.get(period_key)
                if isinstance(period, str):
                    text_without_periods = text_without_periods.replace(period, "")
        for match in number_pattern.finditer(text_without_periods):
            token = match.group().rstrip("%").replace(",", "")
            if Decimal(token) not in valid_numbers:
                raise ValueError("model generated an unsupported number")
    if context.overall and "overall" not in output.overview.evidence_ids:
        raise ValueError("overview missing overall evidence")
    if context.drilldowns and f"path:{context.drilldowns[-1].payload['task_id']}" not in output.overview.evidence_ids:
        raise ValueError("overview missing verified terminal scope")
    if output.decomposition and "decomposition" not in output.decomposition.evidence_ids:
        raise ValueError("decomposition insight missing evidence")
    if output.diagnosis and "diagnosis" not in output.diagnosis.evidence_ids:
        raise ValueError("diagnosis insight missing evidence")
    for item in output.drilldowns:
        if f"path:{item.task_id}" not in item.evidence_ids:
            raise ValueError("drilldown missing own path evidence")


async def enrich_report(report: dict[str, Any], runtime: Any) -> dict[str, Any]:
    """Return a copy; on any LLM or validation failure keep the deterministic report."""
    started = time.monotonic()
    context = context_from_report(report)
    if not context.overall:
        return report
    try:
        parser = JsonOutputParser(pydantic_object=NarrativeOutput)
        prompt = ChatPromptTemplate.from_template(load_prompt("anomaly/report_narrative"))
        llm = get_context_llm(runtime, temperature=0.0, streaming=False, max_retries=0)
        raw = await (prompt | llm | parser).ainvoke({
            "facts_json": json.dumps(context.model_dump(mode="json"), ensure_ascii=False),
            "format_instructions": parser.get_format_instructions(),
        })
        output = NarrativeOutput.model_validate(raw)
        _validate(output, context)
    except Exception as exc:
        # Internal diagnostic only: never surface provider error text or credentials.
        return {**report, "narrative_latency_ms": round((time.monotonic() - started) * 1000),
                "narrative_fallback_reason": type(exc).__name__}
    sections = []
    by_task = {item.task_id: item for item in output.drilldowns}
    for section in report.get("drilldown_sections") or []:
        item = by_task.get(section.get("task_id"))
        sections.append({**section, "narrative_summary": item.text if item else section.get("narrative_summary")})
    decomposition = report.get("decomposition_section")
    if decomposition and output.decomposition:
        decomposition = {**decomposition, "summary": output.decomposition.text}
    result = {**report, "overview_insight": output.overview.text,
              "decomposition_section": decomposition, "drilldown_sections": sections,
              "narrative_source": "llm", "narrative_latency_ms": round((time.monotonic() - started) * 1000),
              "narrative_evidence_ids": {
                  "overview": output.overview.evidence_ids,
                  "decomposition": output.decomposition.evidence_ids if output.decomposition else [],
                  "drilldowns": {item.task_id: item.evidence_ids for item in output.drilldowns},
                  "diagnosis": output.diagnosis.evidence_ids if output.diagnosis else [],
              }}
    if output.diagnosis:
        result["diagnosis_insight"] = output.diagnosis.text
    return result
