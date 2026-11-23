import asyncio
import unittest
from dataclasses import dataclass

from app.agent.anomaly_agent.DimensionAttribution_Agent.scheduler import (
    DynamicTaskScheduler, RequestBudget, TaskExecutionResult,
)


@dataclass(frozen=True)
class Task:
    task_id: str
    parent_task_id: str | None = None
    depth: int = 0


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_six_workers_and_dynamic_children(self):
        budget = RequestBudget(100)
        scheduler = DynamicTaskScheduler[Task](max_workers=6, budget=budget)
        active = 0
        peak = 0
        lock = asyncio.Lock()

        async def execute(task):
            nonlocal active, peak
            async with lock:
                active += 1; peak = max(peak, active)
            await asyncio.sleep(0.02 if task.task_id != "p0" else 0.005)
            async with lock:
                active -= 1
            children = (Task("p0-child", "p0", 1),) if task.task_id == "p0" else ()
            return TaskExecutionResult(task.task_id, task.parent_task_id, "completed", children=children)

        roots = [Task(f"p{i}") for i in range(8)]
        results = await scheduler.run(roots, task_id=lambda x: x.task_id, parent_id=lambda x: x.parent_task_id, execute=execute)
        self.assertEqual(scheduler.max_observed_concurrency, 6)
        self.assertEqual(peak, 6)
        self.assertIn("p0-child", {x.task_id for x in results})

    async def test_budget_is_atomic(self):
        budget = RequestBudget(10)
        self.assertEqual(await asyncio.gather(*[budget.reserve(2) for _ in range(5)]), [True] * 5)
        self.assertFalse(await budget.reserve(2))

    async def test_duplicate_task_runs_once_and_failure_isolated(self):
        scheduler = DynamicTaskScheduler[Task](max_workers=6)
        calls = []
        async def execute(task):
            calls.append(task.task_id)
            if task.task_id == "bad":
                raise RuntimeError("boom")
            return TaskExecutionResult(task.task_id, task.parent_task_id, "completed")
        results = await scheduler.run([Task("ok"), Task("bad"), Task("ok")], task_id=lambda x:x.task_id, parent_id=lambda x:x.parent_task_id, execute=execute)
        self.assertEqual(calls.count("ok"), 1)
        self.assertEqual({x.status for x in results}, {"completed", "failed"})
