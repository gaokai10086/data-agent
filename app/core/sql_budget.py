"""Count actual DW result-query attempts within one attribution tool call."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator


@dataclass
class SQLAttemptBudget:
    limit: int
    count: int = 0

    def before_execute(self) -> None:
        if self.count >= self.limit:
            raise RuntimeError("维度归因 SQL 查询预算已耗尽")
        self.count += 1


_active_budget: ContextVar[SQLAttemptBudget | None] = ContextVar(
    "attribution_sql_attempt_budget", default=None
)


@contextmanager
def sql_attempt_budget(limit: int) -> Iterator[SQLAttemptBudget]:
    budget = SQLAttemptBudget(limit=limit)
    token = _active_budget.set(budget)
    try:
        yield budget
    finally:
        _active_budget.reset(token)


def count_sql_attempt() -> None:
    budget = _active_budget.get()
    if budget is not None:
        budget.before_execute()
