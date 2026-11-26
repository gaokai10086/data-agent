"""Bounded dependency driven scheduler for dimension attribution branches."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from time import monotonic
from typing import Awaitable, Callable, Generic, TypeVar

T = TypeVar("T")

@dataclass(frozen=True)
class ParentEvidence:
    parent_task_id: str | None
    parent_scope: tuple[object, ...]
    target_value: object
    comparison_value: object
    change_amount: object
    reconciled: bool

@dataclass(frozen=True)
class ScheduledTask(Generic[T]):
    task: T
    parent_evidence: ParentEvidence | None = None


@dataclass(frozen=True)
class TaskExecutionResult(Generic[T]):
    task_id: str
    parent_task_id: str | None
    status: str
    query_count: int = 0
    result: T | None = None
    children: tuple[T, ...] = ()
    error: str | None = None
    elapsed_ms: float = 0.0


@dataclass
class RequestBudget:
    limit: int
    reserved: int = 0
    consumed: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    async def reserve(self, amount: int) -> bool:
        async with self._lock:
            if self.reserved + self.consumed + amount > self.limit:
                return False
            self.reserved += amount
            return True

    async def settle(self, reserved: int, actual: int) -> None:
        async with self._lock:
            self.reserved = max(0, self.reserved - reserved)
            self.consumed += actual


class DynamicTaskScheduler(Generic[T]):
    """One queue, bounded workers, and one deterministic result committer."""

    def __init__(self, *, max_workers: int = 6, budget: RequestBudget | None = None):
        self.max_workers = max(1, max_workers)
        self.budget = budget
        self._queue: asyncio.Queue[T | None] = asyncio.Queue()
        self._queued: set[str] = set()
        self._seen: set[str] = set()
        self._results: dict[str, TaskExecutionResult[T]] = {}
        self._commit_lock = asyncio.Lock()
        self.max_observed_concurrency = 0
        self._active = 0

    async def run(
        self,
        roots: list[T],
        *,
        task_id: Callable[[T], str],
        parent_id: Callable[[T], str | None],
        execute: Callable[[T], Awaitable[TaskExecutionResult[T]]],
    ) -> list[TaskExecutionResult[T]]:
        for task in roots:
            await self.add(task, task_id=task_id)

        async def worker():
            while True:
                task = await self._queue.get()
                if task is None:
                    self._queue.task_done()
                    return
                key = task_id(task)
                started = monotonic()
                active_started = False
                reserved = False
                try:
                    if self.budget and not await self.budget.reserve(2):
                        result = TaskExecutionResult(key, parent_id(task), "blocked", error="query_budget_exhausted")
                        async with self._commit_lock:
                            self._results[key] = result
                        continue
                    reserved = bool(self.budget)
                    self._active += 1
                    active_started = True
                    self.max_observed_concurrency = max(self.max_observed_concurrency, self._active)
                    result = await execute(task)
                    if self.budget:
                        await self.budget.settle(2, result.query_count)
                    if not result.elapsed_ms:
                        result = TaskExecutionResult(**{**result.__dict__, "elapsed_ms": (monotonic() - started) * 1000})
                    async with self._commit_lock:
                        self._results[key] = result
                        for child in result.children:
                            await self.add(child, task_id=task_id)
                except asyncio.CancelledError:
                    if reserved and self.budget:
                        await self.budget.settle(2, 0)
                    raise
                except Exception as exc:
                    if reserved and self.budget:
                        await self.budget.settle(2, 0)
                    async with self._commit_lock:
                        self._results[key] = TaskExecutionResult(key, parent_id(task), "failed", error=str(exc), elapsed_ms=(monotonic() - started) * 1000)
                finally:
                    if active_started:
                        self._active -= 1
                    self._queued.discard(key)
                    self._seen.add(key)
                    self._queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(self.max_workers)]
        try:
            await self._queue.join()
        finally:
            for _ in workers:
                await self._queue.put(None)
            await asyncio.gather(*workers, return_exceptions=True)
        return sorted(self._results.values(), key=lambda item: (item.parent_task_id or "", item.task_id))

    async def add(self, task: T, *, task_id: Callable[[T], str]) -> bool:
        key = task_id(task)
        if key in self._seen or key in self._queued:
            return False
        self._queued.add(key)
        await self._queue.put(task)
        return True
