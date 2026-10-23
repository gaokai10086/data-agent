import sys
import uuid
import logging
from pathlib import Path

from loguru import logger

from app.config.app_config import app_config
from app.core.context import request_id_ctx_var, query_task_ctx_var

log_format = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<magenta>request_id - {extra[request_id]}</magenta>{extra[query_details]} | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)

# 控制台面向开发过程，只展示业务消息；完整上下文仍写入文件日志。
console_log_format = "<level>{extra[query_prefix]}{message}</level>"


def inject_request_id(record):
    scope = query_task_ctx_var.get()
    try:
        request_id = request_id_ctx_var.get()
    except Exception as e:
        request_id = scope.request_id if scope else uuid.uuid4()
    record["extra"]["request_id"] = request_id
    record["extra"].update(
        query_prefix=scope.prefix if scope else "",
        query_task_id=scope.task_id if scope else "",
        query_role=scope.role if scope else "",
        query_period=scope.period if scope else "",
        metric_key=scope.metric_key if scope else "",
        query_details=(f" | task - {scope.task_id} | metric - {scope.metric_key}" if scope else ""),
    )


class QueryTaskLoggingFilter(logging.Filter):
    """为使用标准 logging 的驱动消息加上同一任务前缀。"""

    def filter(self, record):
        scope = query_task_ctx_var.get()
        if scope and not getattr(record, "query_task_tagged", False):
            record.msg = scope.prefix + record.getMessage()
            record.args = ()
            record.query_task_tagged = True
        return True


# asyncmy 的 EXPLAIN 优化说明使用标准 logging；与应用节点采用相同标识。
logging.getLogger("asyncmy").addFilter(QueryTaskLoggingFilter())
logging.getLogger("app.core.llm.factory").addFilter(QueryTaskLoggingFilter())


logger.remove()
logger = logger.patch(inject_request_id)
if app_config.logging.console.enable:
    logger.add(
        sink=sys.stdout,
        level=app_config.logging.console.level,
        format=console_log_format,
    )
if app_config.logging.file.enable:
    path = Path(app_config.logging.file.path)
    path.mkdir(parents=True, exist_ok=True)
    logger.add(
        sink=path / "app.log",
        level=app_config.logging.file.level,
        format=log_format,
        rotation=app_config.logging.file.rotation,
        retention=app_config.logging.file.retention,
        encoding="utf-8"
    )

if __name__ == '__main__':
    logger.info("hello world")
