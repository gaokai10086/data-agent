"""
================================================================================
模块名称：数仓结果结构化展示节点 📊
文件路径：app/agent/nodes/present_result.py
核心职责：
    1. 动态读取 State 元数据字段业务描述，免去人工硬编码字典维护
    2. 引入轻量级 LLM 对 SQL 临时聚合衍生列进行智能语义翻译与表头中文化
    3. 组装下发前端三大结构块协议包 (bi_report)
================================================================================
"""
import json
from typing import Any, Dict, List
from langchain_core.prompts import ChatPromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import get_llm
from app.agent.state import DataAgentState
from app.core.logging import logger


# 修改后代码片段（优先取短别名 alias，杜绝超长描述充当表头）：
def _extract_metadata_mapping(table_infos: List[Dict[str, Any]]) -> Dict[str, str]:
    dynamic_mapping = {}
    for table in table_infos:
        for col in table.get("columns", []):
            name = col.get("name")
            aliases = col.get("alias", [])
            # 优先取简短别名（如 "一级类目"）
            short_name = aliases[0] if aliases and isinstance(aliases, list) else None

            # 若无别名，取描述逗号前的短词；兜底使用英文列名
            if not short_name:
                desc = col.get("description") or col.get("comment") or ""
                short_name = desc.split("，")[0].split("。")[0].strip() if desc else name

            if name and short_name:
                dynamic_mapping[name] = short_name
    return dynamic_mapping


def _localize_chart_config(
    chart_config: dict,
    column_mapping: Dict[str, str],
    formatted_rows: List[Dict[str, Any]],
) -> dict:
    """把图表配置本地化：数据列与坐标轴字段改成中文，并去掉冗余标题。

    图表标题不再重复描述「指标 按 维度」这类显而易见的信息，
    坐标轴本身已经表达了这层含义。
    """
    chart_type = chart_config.get("chart_type")
    if not chart_type or chart_type == "table":
        return chart_config

    localized = dict(chart_config)
    localized["data"] = formatted_rows
    localized["title"] = ""

    x_column = chart_config.get("x_column")
    if x_column:
        localized["x_column"] = column_mapping.get(x_column, x_column)
    localized["y_columns"] = [
        column_mapping.get(column, column)
        for column in (chart_config.get("y_columns") or [])
    ]
    return localized


async def _infer_column_names_by_llm(query: str, missing_columns: List[str]) -> Dict[str, str]:
    """针对 SQL 计算衍生列（如 count、sum 别名），调用 LLM 动态推断其最贴切的中文表头"""
    llm = get_llm(provider="deepseek", model_tier="flash", temperature=0.0)

    prompt = ChatPromptTemplate.from_template(
        "你是一个专业的数据仓库工程师。请根据用户的提问语境，将 SQL 结果集中的英文字段/别名转换为简洁规范的中文业务表头名。\n\n"
        "【用户问题】：{query}\n"
        "【待命名的英文字段列表】：{columns}\n\n"
        "【约束规范】：\n"
        "1. 仅输出一个标准的合法 JSON 字典，键为原英文字段名，值为推断的中文业务表头。\n"
        "2. 表头必须语义专业（如 product_count 翻译为 '商品数量'，total_amount 翻译为 '销售总金额'）。\n"
        "3. 严禁包含 Markdown 格式标记（如 ```json 等），禁止输出任何多余的解释文本。"
    )

    try:
        response = await llm.ainvoke(prompt.format_messages(query=query, columns=str(missing_columns)))
        raw_text = response.content.strip()
        # 清洗可能存在的 markdown 代码块包裹
        if raw_text.startswith("```"):
            raw_text = raw_text.strip("`").replace("json", "").strip()
        return json.loads(raw_text)
    except Exception as e:
        logger.warning(f"⚠️ [PresentResult] LLM 智能表头推断降级: {e}")
        return {col: col for col in missing_columns}


async def present_result(state: DataAgentState, runtime: Runtime[DataAgentContext]) -> Dict[str, Any]:
    writer = getattr(runtime, "stream_writer", None)
    # 并行分支汇聚时，某些 LangGraph 版本可能多次触发本节点。
    # 用 State 标记保证 bi_report 只向下游推送一次。
    if state.get("report_emitted"):
        return {}
    if writer:
        writer({"stage": "生成结构化报告"})

    query = state.get("query", "")
    sql = state.get("sql", "")
    sql_result: List[Dict[str, Any]] = state.get("sql_result", [])
    table_infos = state.get("table_infos", [])
    result_analysis = state.get("result_analysis") or {}
    chart_plan = state.get("chart_plan") or {}
    chart_config = state.get("chart_config") or {}
    insight = state.get("insight") or {}

    # 1. 动态提取涉及的数据来源表名
    source_tables = [t.get("name") for t in table_infos if t.get("name")]

    # 2. 构建动态中文化字典 (元数据召回 + LLM 语义补全)
    column_mapping = _extract_metadata_mapping(table_infos)

    if sql_result and isinstance(sql_result, list):
        sample_keys = list(sql_result[0].keys())
        missing_keys = [k for k in sample_keys if k not in column_mapping]

        # 若存在未在数仓元数据中的衍生列（如 SQL 临时聚合别名），交由 LLM 补齐
        if missing_keys:
            ai_inferred_mapping = await _infer_column_names_by_llm(query, missing_keys)
            column_mapping.update(ai_inferred_mapping)

    # 3. 内存无损置换数据表头并标准化特殊数值类型
    formatted_data = []
    if sql_result and isinstance(sql_result, list):
        for row in sql_result:
            chinese_row = {}
            for k, v in row.items():
                col_name = column_mapping.get(k, k)
                # 智能格式化：若是 Decimal/Float，判断是否能被整除
                if hasattr(v, "as_integer_ratio") or isinstance(v, (int, float)):
                    num_val = float(v)
                    # 如果小数部分全为 0（如 9329818.000000），直接转为 int
                    if num_val.is_integer():
                        chinese_row[col_name] = int(num_val)
                    else:
                        chinese_row[col_name] = round(num_val, 2)
                else:
                    chinese_row[col_name] = v
            formatted_data.append(chinese_row)

    # 图表配置本地化：坐标轴改中文，并去掉冗余标题
    if chart_config:
        chart_config = _localize_chart_config(chart_config, column_mapping, formatted_data)

    # 4. 统一通过 Stream 下发结构化协议包
    if writer:
        writer({
            "type": "bi_report",
            "data": formatted_data,  # 1. 结构化数据结果（纯净中文列）
            "sql": sql,  # 2. 底层执行 SQL 语句
            "source_tables": source_tables,  # 2. 涉及的物理表来源审计
            "row_count": len(formatted_data),
            "analysis": result_analysis,
            "chart_plan": chart_plan,
            "chart_config": chart_config,
            "insight": insight,
        })

    logger.info(f"✨ [PresentResult] 结构化报告协议下发完毕，成功动态渲染 {len(formatted_data)} 条记录")
    return {"report_emitted": True}
