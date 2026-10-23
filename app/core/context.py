import contextvars
from contextlib import contextmanager
from dataclasses import dataclass
from itertools import count

request_id_ctx_var = contextvars.ContextVar('request_id')
_query_task_numbers = count(1)


@dataclass(frozen=True)
class QueryTaskContext:
    """一次查询任务的日志标识；独立于业务 State，随异步子任务传播。"""

    request_id: str
    task_id: str
    role: str
    period: str
    metric_key: str
    task_number: int

    @property
    def prefix(self) -> str:
        return f"[data-agent-{self.task_number}] "


query_task_ctx_var = contextvars.ContextVar("query_task", default=None)


@contextmanager
def query_task_context(*, request_id: str, role: str, period: str, metric_key: str):
    """每个并行任务拥有独立上下文；正常结束、异常或取消时都恢复上下文。"""
    scope = QueryTaskContext(request_id=request_id, task_id=f"{request_id}:{role}:{period}",
                             role=role, period=period, metric_key=metric_key,
                             task_number=next(_query_task_numbers))
    token = query_task_ctx_var.set(scope)
    try:
        yield scope
    finally:
        query_task_ctx_var.reset(token)
