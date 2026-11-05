"""SQL 阶段的保守分类与有限技术重试，不重建驱动的连接管理机制。"""
import asyncio
import errno
import re
from collections.abc import Awaitable, Callable
from typing import TypeVar

from sqlalchemy.exc import DBAPIError, TimeoutError as PoolTimeout

from app.core.logging import logger
from app.core.sql_safety import SQLSafetyError

T = TypeVar("T")
DB_ATTEMPTS = 3  # 首次尝试 + 两次技术重试，与 LLM 失败计数独立。
DB_TIMEOUT_SECONDS = 15.0
DB_RETRY_DELAY_SECONDS = 0.25


class SQLReferenceError(ValueError):
    """候选 Schema、值或 Join 约束失败，可交给 SQL 纠错。"""


def classify_sql_error(exc: Exception) -> str:
    if isinstance(exc, SQLSafetyError):
        return "safety"
    if isinstance(exc, SQLReferenceError):
        return "repairable"
    original = exc.orig if isinstance(exc, DBAPIError) else exc
    code = original.args[0] if original.args and isinstance(original.args[0], int) else None
    # 以驱动代码为依据，不用字符串中的 SQL/timeout/denied 猜测恢复动作。
    if code in {1044, 1045, 1142, 1143, 1227, 1370, 1698}:
        return "permission"
    if code in {1052, 1054, 1064, 1109, 1146, 1149}:
        return "repairable"
    if code in {2002, 2003, 2006, 2013, 2055}:
        return "connection"
    if isinstance(original, (TimeoutError, PoolTimeout)):
        return "timeout"
    if isinstance(exc, DBAPIError) and exc.connection_invalidated:
        return "connection"
    if isinstance(original, ConnectionError) or (
        isinstance(original, OSError)
        and original.errno in {errno.ECONNREFUSED, errno.ECONNRESET, errno.EHOSTUNREACH, errno.ENETUNREACH}
    ):
        return "connection"
    return "unknown"


def safe_error_detail(exc: Exception) -> str:
    # 不渲染 SQLAlchemy 的 statement/parameters；只保留驱动原始错误。
    original = exc.orig if isinstance(exc, DBAPIError) else exc
    detail = str(original)
    detail = re.sub(r"(\w+(?:\+\w+)?://)[^\s/@]+(?::[^\s/@]*)?@", r"\1[REDACTED]@", detail)
    detail = re.sub(r"(?i)(password|passwd|pwd)(\s*[=:]\s*)(?:'[^']*'|\"[^\"]*\"|[^\s,;]+)", r"\1\2[REDACTED]", detail)
    return detail[:1000]


def sql_failure(state: dict, exc: Exception, stage: str) -> dict:
    kind = classify_sql_error(exc)
    detail = safe_error_detail(exc)
    logger.error("SQL {} failed: category={} exception={} detail={}", stage, kind, type(exc).__name__, detail)
    return {
        "error": detail or type(exc).__name__,
        "sql_error_kind": kind,
        "sql_error_stage": stage,
        "retry_count": state.get("retry_count", 0) + (kind == "repairable"),
        "sql_result": None,
    }


async def run_sql_operation(repository, operation: Callable[[], Awaitable[T]]) -> T:
    """只重放明确的连接故障；超时终止，不把慢查询当作连接故障重复执行。"""
    for attempt in range(DB_ATTEMPTS):
        try:
            async with asyncio.timeout(DB_TIMEOUT_SECONDS):
                return await operation()
        except Exception as exc:
            # 失联/超时后，SQLAlchemy Session 必须先清理事务才能重新获取连接。
            # 回滚本身失败时停止重试，保留原始异常交给上层分类。
            session = getattr(repository, "dw_session", None)
            if session is not None:
                try:
                    async with asyncio.timeout(DB_TIMEOUT_SECONDS):
                        await session.rollback()
                except Exception as cleanup_error:
                    logger.error("SQL session rollback failed: {}", safe_error_detail(cleanup_error))
                    raise exc from None
            if classify_sql_error(exc) != "connection" or attempt + 1 >= DB_ATTEMPTS:
                raise
            logger.warning("SQL temporary connection failure; retry={}/{} detail={}",
                           attempt + 1, DB_ATTEMPTS - 1, safe_error_detail(exc))
            await asyncio.sleep(DB_RETRY_DELAY_SECONDS * (2 ** attempt))
