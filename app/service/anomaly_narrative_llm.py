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
            "selected_member": s.get("selected_member"), "selected_members": s.get("selected_members"),
            "parent_task_id": s.get("parent_task_id"), "parent_scope": s.get("parent_scope"),
            "actual_decline_coverage": s.get("actual_decline_coverage"),
            "uncovered_decline_amount": s.get("uncovered_decline_amount"),
            "rows": s.get("rows"), "reconciliation": s.get("reconciliation"),
        }) for s in sections if s.get("task_id") and (s.get("selected_member") or s.get("selected_members")) and all(
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

    def approved_percentages(value: Any, key: str = "") -> set[Decimal]:
        if isinstance(value, dict):
            return set().union(*(approved_percentages(item, child_key) for child_key, item in value.items()
                                 if child_key not in {"sql", "sql_evidence", "sql_by_period"}))
        if isinstance(value, list):
            return set().union(*(approved_percentages(item, key) for item in value))
        if "rate" not in key and "贡献率" not in key and "变化率" not in key:
            return set()
        try:
            number = Decimal(str(value))
            return {number * 100, abs(number * 100)} if number.is_finite() else set()
        except (InvalidOperation, TypeError, ValueError):
            return set()

    number_pattern = re.compile(r"(?<![0-9A-Za-z_.])[-+]?\d[\d,]*(?:\.\d+)?(?:%|％|万(?:元)?|亿(?:元)?)?")
    path_facts = {fact.payload["task_id"]: fact for fact in context.drilldowns}
    known_members = {
        str(row["成员"])
        for fact in context.drilldowns for row in fact.payload.get("rows") or []
        if row.get("成员") is not None
    }
    known_members.update(
        str(scope["member_value"])
        for fact in context.drilldowns for scope in fact.payload.get("parent_scope") or []
        if scope.get("member_value") is not None
    )

    def valid_number(token: str, facts: Any) -> bool:
        token = token.replace(",", "")
        is_percent = token.endswith(("%", "％"))
        unit = Decimal("100000000") if "亿" in token else Decimal("10000") if "万" in token else Decimal("1")
        number = Decimal(re.sub(r"[%％万元亿]", "", token))
        if is_percent:
            return any(abs(number - candidate) <= Decimal("0.005") for candidate in approved_percentages(facts))
        if unit > 1:
            return any(abs(number * unit - candidate) <= unit * Decimal("0.005") for candidate in approved_numbers(facts))
        return number in approved_numbers(facts)

    for insight in insights:
        if not insight.text.strip() or not insight.evidence_ids or not set(insight.evidence_ids) <= allowed:
            raise ValueError("invalid evidence references")
        if re.search(r"\b(?:select|from|where)\b", insight.text, re.I):
            raise ValueError("model generated SQL")
        cited_facts = [fact.payload for fact in [context.overall, context.decomposition, *context.drilldowns, context.diagnosis]
                       if fact and fact.evidence_id in insight.evidence_ids]
        cited_members = {
            str(row["成员"]) for fact in context.drilldowns if fact.evidence_id in insight.evidence_ids
            for row in fact.payload.get("rows") or [] if row.get("成员") is not None
        }
        cited_members.update(
            str(scope["member_value"]) for fact in context.drilldowns if fact.evidence_id in insight.evidence_ids
            for scope in fact.payload.get("parent_scope") or [] if scope.get("member_value") is not None
        )
        text_without_periods = insight.text
        for fact in cited_facts:
            for period_key in ("target_period", "comparison_period"):
                period = fact.get(period_key)
                if isinstance(period, str):
                    text_without_periods = text_without_periods.replace(period, "")
                    match = re.fullmatch(r"(\d{4})-(\d{1,2})", period)
                    if match:
                        year, month = match.groups()
                        for rendered in (f"{year}年{int(month)}月", f"{year}年{int(month):02d}月"):
                            text_without_periods = text_without_periods.replace(rendered, "")
        for match in number_pattern.finditer(text_without_periods):
            if not valid_number(match.group(), cited_facts):
                raise ValueError("model generated an unsupported number")
        # Validate explicit member references against the cited path, then bind
        # numbers in a single-member clause to that member's actual row.
        own_path = path_facts.get(insight.task_id) if isinstance(insight, DrilldownInsight) else None
        for clause in re.split(r"[。；，\n]", text_without_periods):
            quoted = re.findall(r"[“\"]([^”\"]+)[”\"]", clause)
            for name in quoted:
                if (re.search(r"(?:成员|渠道|门店|城市|地区|大区|区域|范围).{0,8}[“\"]" + re.escape(name), clause)
                        or re.search(r"[“\"]" + re.escape(name) + r"[”\"]\s*(?:下降|减少|增长|上升|贡献)", clause)):
                    if name not in cited_members:
                        raise ValueError("model referenced an unknown member")
            # Also catch explicit unquoted member assignments and store names.
            unquoted = re.findall(r"(?:成员|渠道|城市|地区|大区|区域)\s*(?:为|是|=|：|:)\s*([^\s，。；、]+)", clause)
            unquoted += re.findall(r"([^\s，。；、“”]{1,20}(?:门店|旗舰店|商城))(?=\s*(?:下降|减少|增长|上升|贡献))", clause)
            for candidate in unquoted:
                if candidate not in cited_members and candidate not in {"该门店", "此门店", "该旗舰店"}:
                    raise ValueError("model referenced an unknown member")
            mentioned = [name for name in known_members if name and name in clause]
            tokens = [match.group() for match in number_pattern.finditer(clause)]
            if not mentioned and tokens and re.search(r"该成员|此成员|该门店|该渠道", clause):
                if own_path and own_path.payload.get("selected_member"):
                    mentioned = [str(own_path.payload["selected_member"])]
                else:
                    raise ValueError("ambiguous member reference")
            if not mentioned or not tokens:
                continue
            if len(mentioned) != 1:
                raise ValueError("ambiguous member amount binding")
            name = mentioned[0]
            own_rows = (own_path.payload.get("rows") or []) if own_path else []
            owner_is_own_path = any(row.get("成员") == name for row in own_rows)
            matching = [row for fact in context.drilldowns
                        if fact.evidence_id in insight.evidence_ids and (not owner_is_own_path or fact is own_path)
                        for row in fact.payload.get("rows") or [] if row.get("成员") == name]
            if re.search(r"减少|下降|增长|上升", clause) and not re.search(r"由.*(?:变为|降至)|从.*(?:变为|降至)", clause):
                change_facts = [{key: row[key] for key in ("变化金额", "change_amount", "变化率", "change_rate") if key in row}
                                for row in matching]
                binding_facts = change_facts
            else:
                binding_facts = matching
            if not matching or not all(valid_number(token, binding_facts) for token in tokens):
                raise ValueError("member amount does not match cited evidence")
    if context.overall and "overall" not in output.overview.evidence_ids:
        raise ValueError("overview missing overall evidence")
    if context.drilldowns and not any(f"path:{fact.payload['task_id']}" in output.overview.evidence_ids
                                      for fact in context.drilldowns):
        raise ValueError("overview missing verified path evidence")
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
