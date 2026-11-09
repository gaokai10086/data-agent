"""Main_RootCause_Agent 的任务 Schema 定义与注册。"""

from app.agent.anomaly_agent.Main_RootCause_Agent.schemas.task_schemas import (
    TASK_SCHEMA_REGISTRY,
    get_task_schema,
)

__all__ = ["TASK_SCHEMA_REGISTRY", "get_task_schema"]
