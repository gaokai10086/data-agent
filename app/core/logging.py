import sys
import uuid
from pathlib import Path

from loguru import logger

from app.config.app_config import app_config
from app.core.context import request_id_ctx_var

log_format = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<magenta>request_id - {extra[request_id]}</magenta> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)

# 控制台面向开发过程，只展示业务消息；完整上下文仍写入文件日志。
console_log_format = "<level>{message}</level>"


def inject_request_id(record):
    try:
        request_id = request_id_ctx_var.get()
    except Exception as e:
        request_id = uuid.uuid4()
    record["extra"]["request_id"] = request_id


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
