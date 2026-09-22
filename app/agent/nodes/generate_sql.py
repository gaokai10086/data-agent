"""
================================================================================
模块名称：SQL 代码智能生成核心节点 🚀🧠
文件路径：app/agent/nodes/generate_sql.py
核心职责：
    1. 汇总数仓表结构、业务指标、时间锚点、方言环境以及【Few-Shot 金标参考示例】
    2. 增加调试探针，透明化监测实际注入大模型 Prompt 的金标上下文
    3. 通过 LCEL 管道异步调用核心 LLM 生成高精、语法对齐的纯文本 SQL
================================================================================
"""
from typing import List, Dict, Any
import yaml
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.state import DataAgentState
from app.core.logging import logger
from app.prompt.prompt_loader import load_prompt


def _format_sql_examples_for_prompt(examples: List[Dict[str, Any]]) -> str:
    """
    将召回的金标 SQL 对象列表格式化为清晰无噪音的 Prompt 上下文
    优先选用与当前候选物理表吻合 (is_table_aligned=True) 的高质量范例
    """
    if not examples:
        return "暂无直接相关的参考范例。"

    formatted_parts = []
    # 按照是否表对齐、相似度分数倒序排序
    sorted_examples = sorted(
        examples,
        key=lambda x: (x.get("is_table_aligned", False), x.get("score", 0.0)),
        reverse=True
    )

    for idx, ex in enumerate(sorted_examples[:2], start=1):
        pattern = ex.get("pattern", "通用复杂分析")
        question = ex.get("question", "")
        # 去掉结尾分号与首尾空白，规范排版
        clean_sql = ex.get("sql", "").strip().rstrip(";")

        part = (
            f"--- 范例 {idx} [分析模式: {pattern}] ---\n"
            f"业务问题: {question}\n"
            f"参考 SQL:\n"
            f"{clean_sql}"
        )
        formatted_parts.append(part)

    return "\n\n".join(formatted_parts)


async def generate_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """
    生成 SQL 节点主函数
    读取上游四路召回与过滤后的全量上下文，产出最终执行 SQL
    """
    # 1. 向客户端推送流式进度
    writer = runtime.stream_writer if runtime else None
    if writer:
        writer({"stage": "生成SQL"})

    # 2. 情报汇总：防御性获取上下文数据
    table_infos = state.get("table_infos", [])
    table_relations = state.get("table_relations", [])
    metric_infos = state.get("metric_infos", [])
    date_info = state.get("date_info", {})
    db_info = state.get("db_info", {})
    query = state.get("query", "")

    # 🌟 提取并格式化第四路金标 SQL 资产
    raw_examples = state.get("retrieved_examples", [])
    sql_examples = _format_sql_examples_for_prompt(raw_examples)

    # 🔍【关键调试探针】：确认金标数据流是否顺畅抵达 generate_sql 节点！
    logger.info(f"🧐 [GenerateSQL-Probe] 捕获 retrieved_examples 原始条数: {len(raw_examples)}")
    logger.info(f"🧐 [GenerateSQL-Probe] 最终注入 Prompt 的 sql_examples 内容:\n{sql_examples}")

    try:
        # 3. 装配 LCEL 提示词模板
        prompt = PromptTemplate(
            template=load_prompt("generate_sql"),
            input_variables=[
                "table_infos",
                "table_relations",
                "metric_infos",
                "date_info",
                "db_info",
                "sql_examples",
                "query"
            ]
        )
        output_parser = StrOutputParser()
        chain = prompt | llm | output_parser

        # 4. 异步推理调用 (使用 safe_dump 保持层级排版，提升大模型解析准确度)
        result = await chain.ainvoke({
            "table_infos": yaml.safe_dump(table_infos, allow_unicode=True, sort_keys=False) if table_infos else "暂无可用表结构",
            "table_relations": yaml.safe_dump(table_relations, allow_unicode=True, sort_keys=False) if table_relations else "暂无已确认的多表关联关系",
            "metric_infos": yaml.safe_dump(metric_infos, allow_unicode=True, sort_keys=False) if metric_infos else "暂无预定义指标",
            "date_info": yaml.safe_dump(date_info, allow_unicode=True, sort_keys=False) if date_info else "暂无时间锚点",
            "db_info": yaml.safe_dump(db_info, allow_unicode=True, sort_keys=False) if db_info else "MySQL 8.0",
            "sql_examples": sql_examples,
            "query": query
        })

        # 5. 代码纯净度清洗：剔除模型偶然生成的 ```sql 标签或多余分号
        clean_result = result.strip()
        if clean_result.startswith("```"):
            lines = clean_result.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            clean_result = "\n".join(lines).strip()

        # 剔除末尾多余分号（适配部分预检执行引擎）
        clean_result = clean_result.rstrip(";")

        logger.info(f"✨ [GenerateSQL] 成功生成 SQL:\n{clean_result}")
        return {"sql": clean_result}

    except Exception as e:
        logger.error(f"❌ [GenerateSQL] 生成 SQL 失败: {e}")
        raise
