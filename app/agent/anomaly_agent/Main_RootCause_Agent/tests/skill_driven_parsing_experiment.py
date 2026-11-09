"""Skill 驱动动态结构化解析的可行性实验。

本实验不修改正式 Graph，不执行 Reason、ReAct 或真实分析 Tool。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, ConfigDict, Field

PROJECT_ROOT = Path(__file__).resolve().parents[5]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.anomaly_agent.Main_RootCause_Agent.skills.registry import (
    UnsupportedSkillError,
    get_skill_by_metric,
)
from app.agent.anomaly_agent.schemas import FilterCondition, MonthPeriod
from app.config.app_config import DEFAULT_LLM_MODEL, DEFAULT_LLM_PROVIDER
from app.core.llm import get_context_llm

HERE = Path(__file__).resolve().parent
DEFAULT_CASES = HERE / "skill_driven_parsing_cases.json"
DEFAULT_OUTPUT = HERE / "skill_driven_parsing_results.json"


class MetricExtraction(BaseModel):
    model_config = ConfigDict(extra="ignore")
    metrics: list[str] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)


class PeriodComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    metric: str | None = None
    target_period: MonthPeriod | None = None
    comparison_period: MonthPeriod | None = None
    filters: list[FilterCondition] = Field(default_factory=list)
    expected_direction: Literal["decrease", "increase", "unspecified"] = "unspecified"
    missing_info: list[str] = Field(default_factory=list)


class AnomalyLocationRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    metrics: list[str] = Field(default_factory=list)
    relative_time: str | None = None
    period: MonthPeriod | None = None
    dimensions: list[str] = Field(default_factory=list)
    ranking: int | None = None
    missing_info: list[str] = Field(default_factory=list)


class DimensionAttributionRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    metric: str | None = None
    target_period: MonthPeriod | None = None
    comparison_period: MonthPeriod | None = None
    filters: list[FilterCondition] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)


class MetricDecompositionRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    metric: str | None = None
    target_period: MonthPeriod | None = None
    comparison_period: MonthPeriod | None = None
    filters: list[FilterCondition] = Field(default_factory=list)
    decomposition_type: str | None = None
    missing_info: list[str] = Field(default_factory=list)


class HypothesisAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    metric: str | None = None
    target_period: MonthPeriod | None = None
    comparison_period: MonthPeriod | None = None
    hypothesis: str | None = None
    factors: list[str] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)


SCHEMA_MODELS = {
    "period_comparison": PeriodComparisonRequest,
    "anomaly_location": AnomalyLocationRequest,
    "dimension_attribution": DimensionAttributionRequest,
    "metric_decomposition": MetricDecompositionRequest,
    "hypothesis_analysis": HypothesisAnalysisRequest,
}

# 真实已注册能力；experimental 表示本实验临时声明，不代表生产能力。
SKILL_SCHEMA_CONFIG = {
    "sales_amount_anomaly": {
        "production": [
            "period_comparison",
            "dimension_attribution",
            "metric_decomposition",
        ],
        "experimental": ["anomaly_location", "hypothesis_analysis"],
    }
}

METRIC_PROMPT = ChatPromptTemplate.from_template(
    """只从用户问题中提取明确提到的业务指标名称，不判断分析路径、不补充指标。
返回 JSON：{{"metrics": ["指标名"], "missing_info": []}}；没有明确指标时 metrics 为空数组。
用户问题：{query}"""
)

SCHEMA_PROMPTS = {
    "period_comparison": ChatPromptTemplate.from_template(
        """把用户问题解析为双周期比较任务。只提取用户明确给出的年月，无法确定时对应字段填 null 并写入 missing_info。
JSON 字段类型必须严格如下：
- metric: 字符串或 null
- target_period / comparison_period: 明确月份时返回 {{"year": 整数, "month": 整数}}，否则 null
- filters: 数组，元素为 {{"dimension": "维度名或null", "value": "值", "operator": "="}}
- expected_direction: 只能取 "decrease"、"increase"、"unspecified"
- missing_info: 字符串数组
用户问题：{query}"""
    ),
    "anomaly_location": ChatPromptTemplate.from_template(
        """把用户问题解析为异常定位任务。只提取用户明确给出的相对时间、绝对周期、指标和关注维度。
JSON 字段类型必须严格如下：
- metrics: 字符串数组
- relative_time: 字符串或 null
- period: 明确月份时返回 {{"year": 整数, "month": 整数}}，否则 null
- dimensions: 字符串数组
- ranking: 整数或 null
- missing_info: 字符串数组
用户问题：{query}"""
    ),
    "dimension_attribution": ChatPromptTemplate.from_template(
        """把用户问题解析为维度归因任务。提取指标、周期、筛选条件和用户关注的维度。
JSON 字段类型必须严格如下：
- metric: 字符串或 null
- target_period / comparison_period: 明确月份时返回 {{"year": 整数, "month": 整数}}，否则 null
- filters: 数组，元素为 {{"dimension": "维度名或null", "value": "值", "operator": "="}}
- dimensions: 字符串数组
- missing_info: 字符串数组
用户问题：{query}"""
    ),
    "metric_decomposition": ChatPromptTemplate.from_template(
        """把用户问题解析为指标拆解任务。提取指标、周期、筛选条件，并记录用户想拆解的关系。
JSON 字段类型必须严格如下：
- metric: 字符串或 null
- target_period / comparison_period: 明确月份时返回 {{"year": 整数, "month": 整数}}，否则 null
- filters: 数组，元素为 {{"dimension": "维度名或null", "value": "值", "operator": "="}}
- decomposition_type: 字符串或 null
- missing_info: 字符串数组
用户问题：{query}"""
    ),
    "hypothesis_analysis": ChatPromptTemplate.from_template(
        """把用户问题解析为业务假设验证任务。提取指标、周期、假设和可能影响因素，不编造假设。
JSON 字段类型必须严格如下：
- metric: 字符串或 null
- target_period / comparison_period: 明确月份时返回 {{"year": 整数, "month": 整数}}，否则 null
- hypothesis: 字符串或 null
- factors: 字符串数组
- missing_info: 字符串数组
用户问题：{query}"""
    ),
}


async def extract_metrics(question: str, context: dict[str, str]) -> MetricExtraction:
    llm = get_context_llm(
        SimpleNamespace(context=context),
        temperature=0.0,
        streaming=False,
    )
    parser = JsonOutputParser(pydantic_object=MetricExtraction)
    extracted = await (METRIC_PROMPT | llm | parser).ainvoke({"query": question})
    return MetricExtraction.model_validate(extracted)


def match_skills(metrics: list[str]) -> dict[str, Any]:
    if not metrics:
        return {"status": "unsupported", "matched_skills": [], "unmatched_metrics": []}
    matched: list[str] = []
    unmatched: list[str] = []
    for metric in metrics:
        try:
            skill = get_skill_by_metric(metric, intent="anomaly_analysis")
            if skill.name not in matched:
                matched.append(skill.name)
        except (UnsupportedSkillError, ValueError):
            unmatched.append(metric)
    if not matched:
        status = "unsupported"
    elif unmatched:
        status = "partial"
    else:
        status = "supported"
    return {
        "status": status,
        "matched_skills": matched,
        "unmatched_metrics": unmatched,
    }


def select_schema(question: str, skill_match: dict[str, Any]) -> dict[str, Any]:
    skills = skill_match.get("matched_skills") or []
    if not skills:
        return {"candidates": [], "selected": None, "reason": "no_matched_skill"}
    skill_name = skills[0]
    config = SKILL_SCHEMA_CONFIG.get(skill_name, {})
    candidates: list[str] = []
    if any(word in question for word in ("假设", "是不是", "导致")):
        candidates.append("hypothesis_analysis")
    if any(word in question for word in ("拆解", "构成", "还是")):
        candidates.append("metric_decomposition")
    if any(word in question for word in ("最近", "波动", "异常")) and "比" not in question:
        candidates.append("anomaly_location")
    if any(word in question for word in ("渠道", "城市", "地区", "影响最大", "最严重")):
        candidates.append("dimension_attribution")
    if any(word in question for word in ("比", "同期", "差异")):
        candidates.append("period_comparison")
    if not candidates:
        candidates.append("period_comparison")

    production = config.get("production", [])
    experimental = config.get("experimental", [])
    candidates = [item for item in candidates if item in production or item in experimental]
    selected = candidates[0] if candidates else None
    return {
        "candidates": candidates,
        "selected": selected,
        "reason": "keyword_priority" if selected else "no_supported_schema",
    }


async def parse_by_schema(
    question: str,
    schema: str,
    context: dict[str, str],
) -> tuple[dict[str, Any] | None, str | None]:
    llm = get_context_llm(
        SimpleNamespace(context=context),
        temperature=0.0,
        streaming=False,
    )
    model = SCHEMA_MODELS[schema]
    parser = JsonOutputParser(pydantic_object=model)
    extracted = await (SCHEMA_PROMPTS[schema] | llm | parser).ainvoke({"query": question})
    parsed = model.model_validate(extracted)
    return parsed.model_dump(mode="json"), None


async def run_case(case: dict[str, Any], context: dict[str, str]) -> dict[str, Any]:
    question = case["question"]
    result: dict[str, Any] = {
        "id": case["id"],
        "raw_question": question,
        "skill_match_accuracy": "pending",
        "schema_selection_accuracy": "pending",
        "semantic_status": "pending",
    }
    try:
        extraction = await extract_metrics(question, context)
        result["extracted_metrics"] = extraction.model_dump(mode="json")
    except Exception as exc:
        result["extracted_metrics"] = None
        result["error"] = f"metric_extraction: {type(exc).__name__}: {exc}"
        return result

    metrics = extraction.metrics
    result["skill_match"] = match_skills(metrics)
    result["schema_selection"] = select_schema(question, result["skill_match"])

    selected = result["schema_selection"]["selected"]
    if selected is None:
        result["parsed_request"] = None
        result["schema_validation"] = {"valid": False, "errors": ["no_supported_schema"]}
        result["missing_info"] = extraction.missing_info
        return result

    try:
        parsed, _ = await parse_by_schema(question, selected, context)
        result["parsed_request"] = parsed
        result["schema_validation"] = {"valid": True, "errors": []}
        result["error"] = None
        result["missing_info"] = parsed.get("missing_info", [])
    except Exception as exc:
        result["parsed_request"] = None
        result["schema_validation"] = {"valid": False, "errors": [f"{type(exc).__name__}: {exc}"]}
        result["error"] = f"schema_parse: {type(exc).__name__}: {exc}"
        result["missing_info"] = extraction.missing_info
    return result


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
        "cases": results,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[REPORT] {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
