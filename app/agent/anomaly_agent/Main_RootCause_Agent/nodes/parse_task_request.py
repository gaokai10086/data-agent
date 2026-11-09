"""按选中 Schema 完成结构化解析并做 Pydantic 校验。"""

from __future__ import annotations

import json

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langgraph.runtime import Runtime
from pydantic import ValidationError

from app.agent.anomaly_agent.Main_RootCause_Agent.state import RootCauseState
from app.agent.anomaly_agent.Main_RootCause_Agent.schemas import get_task_schema
from app.agent.data_agent.context import DataAgentContext
from app.core.llm import get_context_llm
from app.prompt.prompt_loader import load_prompt


async def parse_task_request(
    state: RootCauseState,
    runtime: Runtime[DataAgentContext],
) -> dict[str, object]:
    """按选中 Schema 解析并校验；不调用变化检验 Tool 或执行 SQL。"""
    schema_result = state.get("schema_result") or {}
    task_type = schema_result.get("task_type")
    if not task_type:
        raise ValueError("parse_task_request 需要先完成 Schema 选择")

    schema = get_task_schema(task_type)
    llm = get_context_llm(runtime, temperature=0.0, streaming=False)
    prompt = ChatPromptTemplate.from_template(load_prompt("anomaly/parse_task_request"))
    parser = JsonOutputParser(pydantic_object=schema)
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    extracted = await (prompt | llm | parser).ainvoke({
        "schema_json": schema_json,
        "query": state["raw_question"],
    })

    try:
        parsed = schema.model_validate(extracted)
    except ValidationError as exc:
        return {
            "parsed_result": {
                "status": "failed",
                "parsed_request": None,
                "errors": [str(exc)],
                "missing_info": [],
            },
        }

    return {
        "parsed_result": {
            "status": "passed",
            "parsed_request": parsed.model_dump(mode="json"),
            "errors": [],
            "missing_info": getattr(parsed, "missing_info", []),
        },
    }
