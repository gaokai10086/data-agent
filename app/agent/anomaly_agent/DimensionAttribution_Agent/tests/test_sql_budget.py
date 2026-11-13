import asyncio
import unittest

from app.core.sql_budget import sql_attempt_budget
from app.repositories.mysql.dw_mysql_repository import DWMySQLRepository
from app.service.sql_error_handling import run_sql_operation


class _Rows:
    def fetchall(self):
        return [{"value": 1}]


class _Result:
    def mappings(self):
        return _Rows()


class _Session:
    def __init__(self):
        self.calls = 0
        self.rollbacks = 0

    async def execute(self, _sql):
        self.calls += 1
        if self.calls == 1:
            raise ConnectionError("temporary")
        return _Result()

    async def rollback(self):
        self.rollbacks += 1


class SQLBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_periods_share_one_budget_without_sharing_sessions(self):
        sessions = [_Session(), _Session()]
        repositories = [DWMySQLRepository(session) for session in sessions]
        with sql_attempt_budget(2) as budget:
            await asyncio.gather(*(
                repository.execute_sql("SELECT 1 AS value")
                for repository in repositories
            ), return_exceptions=True)
        self.assertEqual(budget.count, 2)
        self.assertEqual([session.calls for session in sessions], [1, 1])

    async def test_connection_retry_counts_each_actual_attempt(self):
        session = _Session()
        repository = DWMySQLRepository(session)
        with sql_attempt_budget(2) as budget:
            rows = await run_sql_operation(repository, lambda: repository.execute_sql("SELECT 1 AS value"))
        self.assertEqual(rows, [{"value": 1}])
        self.assertEqual(budget.count, 2)
        self.assertEqual(session.calls, 2)
        self.assertEqual(session.rollbacks, 1)

    async def test_budget_blocks_retry_before_database_call(self):
        session = _Session()
        repository = DWMySQLRepository(session)
        with sql_attempt_budget(1) as budget:
            with self.assertRaisesRegex(RuntimeError, "预算已耗尽"):
                await run_sql_operation(repository, lambda: repository.execute_sql("SELECT 1 AS value"))
        self.assertEqual(budget.count, 1)
        self.assertEqual(session.calls, 1)
