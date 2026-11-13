"""BusinessDiagnosisAgent 独立 Graph 的真实 LLM 验收。

只读取同一次 RootCause 综合测试 C 的 JSON 记录，不执行新的 SQL。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
import traceback
from typing import Any, cast
from pathlib import Path

from app.agent.anomaly_agent.BusinessDiagnosis_Agent.graph import business_diagnosis_graph
from app.agent.anomaly_agent.BusinessDiagnosis_Agent.schemas import BusinessDiagnosisInput
from app.agent.anomaly_agent.Main_RootCause_Agent.skills.registry import load_skill
from app.agent.anomaly_agent.schemas import AnalysisRequest, FilterCondition, InspectionOutcome
from app.agent.data_agent.context import DataAgentContext
from app.config.app_config import DEFAULT_LLM_MODEL, DEFAULT_LLM_PROVIDER

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "diagnostics" / "rootcause_decomposition_c.json"
OUTPUT = ROOT / "diagnostics" / "business_diagnosis_real.json"


def _validate_source_record(record: dict[str, Any]) -> InspectionOutcome:
    """严格拒绝混合请求、周期或固定筛选范围的证据。只读 JSON，不执行 SQL。"""
    inspection_raw = record.get("inspection", {}).get("inspection_outcome")
    attribution = record.get("attribution", {}).get("attribution_result")
    decomposition = record.get("decomposition", {}).get("decomposition_result")
    if not all(isinstance(item, dict) for item in (inspection_raw, attribution, decomposition)):
        raise ValueError("来源记录必须同时包含 inspection、attribution 和 decomposition 结果")
    inspection = InspectionOutcome.model_validate(inspection_raw)
    request_id = inspection.request_id
    if not request_id:
        raise ValueError("InspectionOutcome 缺少 request_id")
    for name, result in (("attribution", attribution), ("decomposition", decomposition)):
        if result.get("request_id") != request_id:
            raise ValueError(f"{name} 与 InspectionOutcome 的 request_id 不一致")
    overall = inspection.overall_result
    metric = inspection.metric_context
    if overall is None or metric is None or overall.metric_key != metric.metric_key:
        raise ValueError("总体变化证据与 MetricContext 缺失或指标不一致")
    periods = (overall.target_period, overall.comparison_period)
    sqls: list[str] = []
    for result in (attribution, decomposition):
        for observation in result.get("observations", []):
            evidence = observation.get("evidence") or {}
            values = evidence.get("values") or {}
            sqls.extend(str(value) for value in (values.get("sql_by_period") or {}).values())
            sqls.extend(str(value) for value in (evidence.get("sql_by_period") or {}).values())
        for path in result.get("paths", []):
            for observation in path.get("observations", []):
                evidence = observation.get("evidence") or {}
                sqls.extend(str(value) for value in (evidence.get("sql_by_period") or {}).values())
    if not sqls:
        raise ValueError("来源记录没有可核验的 SQL 证据")
    seen_periods: set[str] = set()
    for sql in sqls:
        normalized = re.sub(r"\s+", " ", sql).lower()
        if "北京" not in sql or "city" not in normalized or "pay_amount" not in normalized or "order_status" not in normalized:
            raise ValueError("证据 SQL 未体现北京城市过滤、销售额字段或已完成订单口径")
        matched = [period for period in periods if period in sql]
        if not matched:
            raise ValueError("证据 SQL 未覆盖总体结果的目标/对比周期")
        seen_periods.update(matched)
    if seen_periods != set(periods):
        raise ValueError("来源 SQL 证据未同时覆盖目标周期和对比周期")
    return inspection


def _load_input() -> tuple[BusinessDiagnosisInput, dict]:
    record = json.loads(SOURCE.read_text(encoding="utf-8"))
    inspection = _validate_source_record(record)
    assert inspection.overall_result is not None
    request = AnalysisRequest(
        metric=inspection.metric_context.name,
        request_id=inspection.request_id,
        target_period=inspection.overall_result.target_period,
        comparison_period=inspection.overall_result.comparison_period,
        comparison_type="mom",
        filters=[FilterCondition(dimension="城市", value="北京")],
        expected_direction=inspection.verification.expected_direction,
    )
    attribution = record["attribution"].get("attribution_result")
    decomposition = record["decomposition"].get("decomposition_result")
    skill = load_skill("sales_amount_anomaly")
    data = BusinessDiagnosisInput(
        raw_question=record["question"],
        analysis_request=request,
        inspection_outcome=inspection,
        attribution_result=attribution,
        decomposition_result=decomposition,
        metric_context=inspection.metric_context,
        skill_strategy=skill.strategy,
        available_tool_names=(),
    )
    return data, record


async def main() -> None:
    started = time.monotonic()
    report: dict = {
        "status": "failed",
        "source_record": str(SOURCE),
        "output_record": str(OUTPUT),
        "sql_query_count": 0,
    }
    try:
        input_data, source = _load_input()
        state: dict = {}
        nodes: list[str] = []
        # 第一阶段 Graph 只消费 LLM 配置，不访问 Repository；仍按生产约定使用 DataAgentContext 类型。
        context = cast(DataAgentContext, {
            "llm_provider": DEFAULT_LLM_PROVIDER,
            "llm_model_tier": DEFAULT_LLM_MODEL,
        })
        stream = business_diagnosis_graph.astream(
            {"input": input_data},
            context=context,
            stream_mode=["updates", "values"],
        )
        try:
            async for mode, chunk in stream:
                if mode == "updates" and isinstance(chunk, dict):
                    nodes.extend(str(name) for name in chunk)
                elif mode == "values" and isinstance(chunk, dict):
                    state.update(chunk)
        finally:
            await stream.aclose()
        result = state["result"]
        result_json = result.model_dump_json(indent=2, ensure_ascii=False)
        OUTPUT.write_text(result_json + "\n", encoding="utf-8")
        evidence_ids = set(result.evidence_ids)
        findings_traceable = all(set(item.evidence_ids) <= evidence_ids for item in result.findings)
        hypotheses_unverified = all(item.status in {"hypothesis", "evidence_gap"} and not item.executable for item in result.hypotheses)
        llm_success = not any(item.status == "failed" for item in state.get("observations", []))
        report.update({
            "status": "passed" if findings_traceable and hypotheses_unverified and llm_success and result.status == "completed" else "failed",
            "request_id": input_data.analysis_request.request_id,
            "source_request_ids": {
                "inspection": source["inspection"]["inspection_outcome"]["request_id"],
                "attribution": source["attribution"]["attribution_result"]["request_id"],
                "decomposition": source["decomposition"]["decomposition_result"]["request_id"],
            },
            "nodes": nodes,
            "result": result.model_dump(mode="json"),
            "observations": [item.model_dump(mode="json") for item in state.get("observations", [])],
            "findings_traceable": findings_traceable,
            "hypotheses_unverified": hypotheses_unverified,
            "llm_success": llm_success,
            "pending_tasks": [task.get_name() for task in asyncio.all_tasks() if task is not asyncio.current_task() and not task.done()],
        })
    except Exception as exc:
        report.update(exception_type=type(exc).__name__, error=str(exc), traceback=traceback.format_exc())
    finally:
        report["elapsed_ms"] = round((time.monotonic() - started) * 1000, 1)
        # result 文件严格保存 DiagnosisResult；诊断运行元数据另存为 sidecar。
        (OUTPUT.with_suffix(".meta.json")).write_text(json.dumps(report, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    result = report.get("result") or {}
    print(json.dumps({
        "status": report.get("status"),
        "completion_status": result.get("completion_status"),
        "stop_reason": result.get("stop_reason"),
        "findings": result.get("findings", []),
        "hypotheses": result.get("hypotheses", []),
        "evidence_ids": result.get("evidence_ids", []),
        "request_id": report.get("request_id"),
        "nodes": report.get("nodes"),
        "findings_traceable": report.get("findings_traceable"),
        "hypotheses_unverified": report.get("hypotheses_unverified"),
        "pending_tasks": report.get("pending_tasks"),
        "elapsed_ms": report.get("elapsed_ms"),
        "exception_type": report.get("exception_type"),
        "error": report.get("error"),
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BusinessDiagnosisAgent 独立真实 LLM 验收（不执行 SQL）")
    parser.add_argument("--source", type=Path, default=SOURCE, help="同一请求的 RootCause 诊断 JSON")
    args = parser.parse_args()
    SOURCE = args.source if args.source.is_absolute() else ROOT / args.source
    asyncio.run(main())
