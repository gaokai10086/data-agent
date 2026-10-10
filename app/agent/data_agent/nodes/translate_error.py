import re
from app.agent.data_agent.state import DataAgentState
from app.agent.data_agent.context import DataAgentContext
from langgraph.runtime import Runtime
from app.core.logging import logger
from app.core.llm import get_context_llm
from app.prompt.prompt_loader import load_prompt


def _classify_error(state: DataAgentState) -> tuple[str, str]:
    """将内部异常归类为稳定的业务错误类型和失败阶段。"""
    error = str(state.get("error") or "").lower()
    if not state.get("table_infos"):
        return "元数据未就绪", "表结构召回"
    if any(word in error for word in ("timeout", "timed out", "超时")):
        return "查询超时", "数据库执行"
    if any(word in error for word in ("access denied", "permission", "denied", "权限")):
        return "数据权限不足", "SQL 校验或执行"
    if any(word in error for word in ("connect", "connection", "can't connect", "连接")):
        return "数据服务连接失败", "数据库连接"
    if any(word in error for word in ("unknown column", "unknown table", "doesn't exist", "不存在")):
        return "字段或数据表不存在", "SQL 校验"
    if any(word in error for word in ("syntax", "1064", "sql")):
        return "SQL 语法或逻辑错误", "SQL 校验"
    return "查询执行失败", "数据查询"


def _fallback_message(error_type: str) -> str:
    messages = {
        "元数据未就绪": "暂时无法查询：系统尚未加载可用的业务表和字段信息，请联系管理员完成元数据构建。",
        "查询超时": "查询耗时过长，暂时无法完成。请缩小时间范围或减少查询数据量后重试。",
        "数据权限不足": "当前账号没有访问所需数据的权限，请联系管理员核对数据权限。",
        "数据服务连接失败": "暂时无法连接数据服务，请稍后重试；如果问题持续，请联系管理员。",
        "字段或数据表不存在": "查询涉及的数据结构暂未找到，请确认业务名称或联系管理员核对数据表配置。",
        "SQL 语法或逻辑错误": "查询语句未能通过校验，请尝试简化查询条件后重试。",
    }
    return messages.get(error_type, "暂时无法完成这次数据查询，请稍后重试或联系管理员。")


async def _generate_friendly_message(state: DataAgentState) -> str:
    error_type, error_stage = _classify_error(state)
    fallback = _fallback_message(error_type)
    try:
        prompt = load_prompt("data_agent/error_response")
        llm = get_context_llm(runtime, temperature=0.1)
        response = await llm.ainvoke(prompt.format(
            user_query=state.get("query", ""),
            error_type=error_type,
            error_stage=error_stage,
            tech_error=str(state.get("error") or "")[:500],
        ))
        message = str(getattr(response, "content", response)).strip()
        message = re.sub(r"^```(?:text|markdown)?\s*|\s*```$", "", message, flags=re.IGNORECASE).strip()
        if message and len(message) <= 160:
            return message
        logger.warning("错误提示模型输出为空或过长，使用兜底提示")
    except Exception as exc:
        logger.warning(f"错误提示模型调用失败，使用兜底提示: {exc}")
    return fallback


async def translate_error(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    # 1. 从记录本中拿到用户原本的问题和技术报错
    user_query = state.get("query")
    tech_error = state.get("error")
    retry_count = state.get("retry_count", 0)
    is_valid_query = state.get("is_valid_query")

    # 根据错误类型读取专用 Prompt，并调用模型生成用户可理解的提示。
    friendly_message = await _generate_friendly_message(state)

    logger.info("错误信息业务化翻译完成")

    # 5. 同时写回 State，并通过自定义流事件实时推送给前端。
    # 仅写入 State 不会自动出现在 stream_mode="custom" 的 SSE 流中。
    if runtime and getattr(runtime, "stream_writer", None):
        runtime.stream_writer({
            "type": "error_response",
            "content": friendly_message,
            "retry_count": retry_count,
        })
    return {"error_response": friendly_message}
