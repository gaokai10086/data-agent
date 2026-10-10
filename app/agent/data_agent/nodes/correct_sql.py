from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.data_agent.context import DataAgentContext
from app.core.llm import get_context_llm
from app.agent.data_agent.state import DataAgentState
from app.core.logging import logger
from app.prompt.prompt_loader import load_prompt


async def correct_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "校正SQL"})

    #  为什么需要 7 个上下文信息？
    """
    1.LLM需要完整信息才能精确修正
    2.缺少任何一项都可能导致修正错误
    3.与generate_sql 使用相同的上下文
    把 query + sql + error + table_infos 四者合一，大模型就能进行高精度的因果推断
    """
    query = state["query"] #  理解业务意图
    sql = state["sql"]  #  明确待修正的sql
    error = state["error"] #  定位错误位置
    table_infos = state["table_infos"]  #  确保字段名正确
    metric_infos = state["metric_infos"]  #  遵循指标定义
    date_info = state["date_info"]   #  解析相当时间
    db_info = state["db_info"]  #  适配数据库语法

    try:
        #。把 query + sql + error + table_infos 四者合一，大模型就能进行高精度的因果推断
        prompt = PromptTemplate(template=load_prompt("data_agent/correct_sql"),
                                input_variables=["query", "sql", "error", "table_infos", "metric_infos", "date_info",
                                                 "db_info"])
        #  为什么使用 StrOutputParser？
        """
        1. SQL 是纯文本，不是 JSON
        2. 避免 LLM 输出 Markdown 格式
        3. 与 generate_sql 保持一致
        """
        output_parser = StrOutputParser()
        llm = get_context_llm(runtime, temperature=0.1, streaming=False)
        chain = prompt | llm | output_parser

        result = await chain.ainvoke(
            {"query": query, "sql": sql, "error": error, "table_infos": table_infos, "metric_infos": metric_infos,
             "date_info": date_info, "db_info": db_info})
        logger.info(f'校正SQL结果：{result}')
        return {"sql": result}
    except Exception as e:
        logger.error(f'校正SQL失败：{e}')
        raise

"""
Agent 自愈纠错是怎么实现的？
将错题、报错堆栈连同原始提问打包成新的上下文，纠错提示词再次调用大模型
"""
