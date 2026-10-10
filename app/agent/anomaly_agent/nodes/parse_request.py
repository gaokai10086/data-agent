"""使用 LLM 提取请求，再用确定性代码校验和归一化。

LLM 擅长理解自然语言；代码擅长执行月份范围、跨年和比较类型等稳定规则。
"""

from __future__ import annotations

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langgraph.runtime import Runtime

from app.agent.anomaly_agent.schemas import AnalysisRequest, ExtractedRequest
from app.agent.anomaly_agent.state import AnomalyState
from app.agent.data_agent.context import DataAgentContext
from app.core.llm import get_context_llm
from app.prompt.prompt_loader import load_prompt


def _year_month(year: int, month: int) -> str:
    """校验年月并统一成 YYYY-MM；不查询数据库。"""
    if not 1 <= year <= 9999:
        raise ValueError(f"无效年份：{year}")
    if not 1 <= month <= 12:
        raise ValueError(f"无效月份：{month}")
    return f"{year:04d}-{month:02d}"


def validate_extracted_request(fields: ExtractedRequest) -> AnalysisRequest:
    """校验 LLM 字段，并生成下游稳定使用的 AnalysisRequest。"""
    if not fields.metric.strip():
        raise ValueError("无法识别要分析的指标")

    target_period = _year_month(fields.target_year, fields.target_month)
    comparison_year = fields.comparison_year
    if comparison_year is None:
        if fields.comparison_month < fields.target_month:
            comparison_year = fields.target_year
        elif fields.target_month == 1 and fields.comparison_month == 12:
            comparison_year = fields.target_year - 1
        else:
            raise ValueError("对比月份的年份不明确，请让用户写出完整年份")

    comparison_period = _year_month(comparison_year, fields.comparison_month)
    target_index = fields.target_year * 12 + fields.target_month
    comparison_index = comparison_year * 12 + fields.comparison_month
    if target_index == comparison_index:
        raise ValueError("目标月份与对比月份不能相同")

    if comparison_index == target_index - 1:
        comparison_type = "mom"
    elif comparison_year == fields.target_year - 1 and fields.comparison_month == fields.target_month:
        comparison_type = "yoy"
    else:
        comparison_type = "custom"

    return AnalysisRequest(
        metric=fields.metric.strip(),
        filters=fields.filters,
        target_period=target_period,
        comparison_period=comparison_period,
        comparison_type=comparison_type,
    )


async def parse_request(
    state: AnomalyState,
    runtime: Runtime[DataAgentContext],
) -> dict[str, object]:
    """LLM 提取字段，再把代码校验后的请求写回 Graph State。"""
    llm = get_context_llm(runtime, temperature=0.0, streaming=False)
    prompt = ChatPromptTemplate.from_template(load_prompt("anomaly/parse_request"))
    parser = JsonOutputParser(pydantic_object=ExtractedRequest)
    extracted = ExtractedRequest.model_validate(
        await (prompt | llm | parser).ainvoke({"query": state["raw_question"]})
    )
    request = validate_extracted_request(extracted)
    return {
        "analysis_request": request,
    }
