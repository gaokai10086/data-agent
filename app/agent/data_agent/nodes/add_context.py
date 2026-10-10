from datetime import datetime

from langgraph.runtime import Runtime

from app.agent.data_agent.context import DataAgentContext
from app.agent.data_agent.state import DataAgentState, DateInfoState, DBInfoState
from app.core.logging import logger


def format_examples_for_prompt(examples: list[dict]) -> str:
    """将召回的金标 SQL 列表渲染为大模型 Prompt 纯文本"""
    if not examples:
        return "暂无直接相关的参考范例。"

    formatted_parts = []
    # 优先使用与当前底表对齐的高精度样本
    sorted_examples = sorted(examples, key=lambda x: (x.get("is_table_aligned", False), x.get("score", 0.0)),
                             reverse=True)

    for idx, ex in enumerate(sorted_examples[:2], start=1):
        pattern = ex.get("pattern", "通用复杂分析")
        question = ex.get("question", "")
        clean_sql = ex.get("sql", "").strip().rstrip(";")

        part = (
            f"--- 范例 {idx} [计算范式: {pattern}] ---\n"
            f"业务问题: {question}\n"
            f"参考代码:\n"
            f"{clean_sql}"
        )
        formatted_parts.append(part)

    return "\n\n".join(formatted_parts)


async def add_context(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "添加上下文信息"})
    today = datetime.today()

    quarter = f"Q{(today.month - 1) // 3 + 1}"
    date_info = DateInfoState(
        date=today.strftime("%Y-%m-%d"),
        weekday=today.strftime("%A"),
        quarter=quarter,
    )

    db_info = DBInfoState(**await runtime.context['dw_mysql_repository'].get_db_info())

    # 🌟 提取并格式化第四路金标 SQL 资产
    raw_examples = state.get("retrieved_examples", [])
    formatted_examples_str = format_examples_for_prompt(raw_examples)

    logger.info(
        f'✨ [AddContext] 上下文装配就绪: date_info={date_info}, db_info={db_info}, examples_count={len(raw_examples)}')

    return {
        "date_info": date_info,
        "db_info": db_info,
        # 若需要把格式化好的字符串暂存，可直接返回，也可在 generate_sql 内部直接读取 state["retrieved_examples"] 现场渲染
        "formatted_examples": formatted_examples_str
    }