"""指标拆解元数据回归；无真实数据库、模型或 Qdrant 调用。"""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pydantic import ValidationError

from app.agent.anomaly_agent.Change_Inspection_Agent.nodes.load_metric_context import _build_metric_context, load_metric_context
from app.schemas.metric_decomposition import MetricDecomposition, sales_amount_decomposition
from app.schemas.metric_management import MetricSubmitRequest
from app.scripts.sync_sales_decomposition import migrate
from app.service.metric_decomposition_service import validate_metric_decomposition
from app.service.metric_index_rebuild_service import MetricIndexRebuildService
from app.service.metric_submission_service import MetricSubmissionService


def record(key="sales_amount", **changes):
    data = dict(id="a" * 32, metric_key=key, name="销售额" if key == "sales_amount" else "订单数量",
                formula="SUM(fact_order.pay_amount)" if key == "sales_amount" else "COUNT(DISTINCT fact_order.order_sn)",
                fact_table="fact_order", measure_column="pay_amount" if key == "sales_amount" else "order_sn",
                aggregation="SUM" if key == "sales_amount" else "COUNT_DISTINCT", time_column="pay_time",
                filters=[{"column": "fact_order.order_status", "operator": "=", "value": "已完成"}],
                status="published", version=3, decomposition=None, description="定义", alias=[],
                dimensions=[], relevant_columns=[])
    data.update(changes)
    return SimpleNamespace(**data)


class SchemaTest(unittest.TestCase):
    def test_config_round_trip_and_old_metric_compatibility(self):
        config = sales_amount_decomposition()
        self.assertEqual(MetricDecomposition.model_validate_json(config.model_dump_json()), config)
        context = _build_metric_context(record(decomposition=config.model_dump(mode="json")))
        self.assertEqual(context.decomposition, config)
        self.assertEqual(context.formula, "SUM(fact_order.pay_amount)")
        self.assertIsNone(_build_metric_context(record()).decomposition)

    def test_reject_arbitrary_expression_or_unsupported_policy(self):
        for field, value in [("expression", "eval('x')"), ("type", "division"),
                             ("version", 2), ("scope_policy", "reset"),
                             ("zero_denominator_policy", "zero")]:
            raw = sales_amount_decomposition().model_dump(mode="json")
            raw[field] = value
            with self.subTest(field=field), self.assertRaises(ValidationError):
                MetricDecomposition.model_validate(raw)

    def test_reject_duplicate_or_inconsistent_factors(self):
        for change in ("duplicate", "denominator", "self"):
            raw = sales_amount_decomposition().model_dump(mode="json")
            if change == "duplicate":
                raw["factors"][1]["factor_key"] = "order_count"
            elif change == "denominator":
                raw["factors"][1]["source"]["denominator_metric_key"] = "user_count"
            else:
                raw["factors"][1]["source"]["numerator_metric_key"] = "order_count"
            with self.subTest(change=change), self.assertRaises(ValidationError):
                MetricDecomposition.model_validate(raw)


class ValidationTest(unittest.IsolatedAsyncioTestCase):
    async def test_same_scope_and_published_count_pass(self):
        count = record("order_count", time_column="fact_order.pay_time",
                       filters=[{"column": "order_status", "operator": "=", "value": "已完成"}])
        repo = SimpleNamespace(get_metric_by_key=AsyncMock(return_value=count))
        await validate_metric_decomposition(record(), sales_amount_decomposition(), repo)
        repo.get_metric_by_key.assert_awaited_once_with("order_count")

    async def test_no_decomposition_needs_no_reference_lookup(self):
        repo = SimpleNamespace(get_metric_by_key=AsyncMock())
        await validate_metric_decomposition(record(), None, repo)
        repo.get_metric_by_key.assert_not_awaited()

    async def test_invalid_reference_or_scope_rejected(self):
        sources = [None, record("order_count", status="draft"), record("order_count", aggregation="AVG"),
                   record("order_count", fact_table="fact_order_detail"),
                   record("order_count", time_column="created_at"),
                   record("order_count", filters=[])]
        for source in sources:
            repo = SimpleNamespace(get_metric_by_key=AsyncMock(return_value=source))
            with self.subTest(source=source), self.assertRaises(ValueError):
                await validate_metric_decomposition(record(), sales_amount_decomposition(), repo)

    async def test_wrong_target_or_non_sum_rejected(self):
        repo = SimpleNamespace(get_metric_by_key=AsyncMock(return_value=record("order_count")))
        for target in (record(metric_key="other_amount"), record(aggregation="AVG")):
            with self.subTest(target=target), self.assertRaises(ValueError):
                await validate_metric_decomposition(target, sales_amount_decomposition(), repo)

    async def test_agent_loads_config_and_checks_reference(self):
        config = sales_amount_decomposition()
        repo = SimpleNamespace(get_metric_by_name=AsyncMock(return_value=record(decomposition=config.model_dump(mode="json"))),
                               get_metric_by_key=AsyncMock(return_value=record("order_count")))
        runtime = SimpleNamespace(context={"meta_mysql_repository": repo})
        state = {"analysis_request": SimpleNamespace(metric="销售额")}
        result = await load_metric_context(state, runtime)
        self.assertEqual(result["metric_context"].decomposition, config)
        repo.get_metric_by_key.assert_awaited_once_with("order_count")
        repo.get_metric_by_key.return_value.status = "deprecated"
        with self.assertRaises(ValueError):
            await load_metric_context(state, runtime)

    async def test_submission_persists_config(self):
        request = MetricSubmitRequest(
            metric_key="sales_amount", name="销售额", description="已完成订单支付金额",
            formula="SUM(fact_order.pay_amount)", fact_table="fact_order", measure_column="pay_amount",
            aggregation="SUM", time_column="pay_time", filters=record().filters,
            decomposition=sales_amount_decomposition(),
        )
        async def get_metric(key):
            return record("order_count") if key == "order_count" else None
        repo = SimpleNamespace(
            get_metric_by_key=AsyncMock(side_effect=get_metric), get_metric_by_name=AsyncMock(return_value=None),
            get_table_by_name=AsyncMock(return_value=SimpleNamespace(id="table")),
            get_column=AsyncMock(return_value=SimpleNamespace(id="column")),
            save_metric=AsyncMock(), get_column_metric=AsyncMock(return_value=object()),
        )
        saved = await MetricSubmissionService(repo).submit(request)
        self.assertEqual(saved.decomposition, sales_amount_decomposition().model_dump(mode="json"))
        self.assertEqual(saved.formula, request.formula)

    async def test_index_payload_keeps_config(self):
        cfg = sales_amount_decomposition().model_dump(mode="json")
        repo = SimpleNamespace(list_metrics_by_status=AsyncMock(return_value=[record(decomposition=cfg)]))
        embedding = SimpleNamespace(aembed_documents=AsyncMock(return_value=[[0.1, 0.2]]))
        client = SimpleNamespace(collection_exists=AsyncMock(return_value=False),
                                 create_collection=AsyncMock(), upsert=AsyncMock())
        await MetricIndexRebuildService(repo, embedding, client, vector_size=2).rebuild()
        point = client.upsert.await_args.kwargs["points"][0]
        self.assertEqual(point.payload["decomposition"], cfg)


class FakeConnection:
    def __init__(self, exists=False, config=None, source=None):
        self.exists = exists
        self.sales = vars(record(decomposition=config)).copy()
        self.source = source or record("order_count")
        self.calls = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params))
        if "information_schema.columns" in sql:
            return SimpleNamespace(scalar=lambda: int(self.exists))
        if "FROM metric_info WHERE" in sql:
            # 模拟 asyncmy text 查询中 JSON 以字符串返回。
            rows = [dict(self.sales), vars(self.source).copy()]
            for row in rows:
                row["filters"] = json.dumps(row["filters"], ensure_ascii=False)
                if row["decomposition"] is not None:
                    row["decomposition"] = json.dumps(row["decomposition"], ensure_ascii=False)
            return SimpleNamespace(mappings=lambda: rows)
        if "ALTER TABLE" in sql:
            self.exists = True
        if "UPDATE metric_info" in sql:
            self.sales["decomposition"] = json.loads(params["config"])
            self.sales["version"] += 1
            return SimpleNamespace(rowcount=1)
        return SimpleNamespace()


class MigrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_preview_has_no_ddl_dml_or_snapshot(self):
        connection = FakeConnection()
        with TemporaryDirectory() as directory:
            report = await migrate(connection, snapshot_dir=Path(directory))
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertFalse(report["apply"])
        self.assertTrue(report["column_will_be_added"])
        self.assertFalse(any("ALTER TABLE" in sql or "UPDATE metric_info" in sql for sql, _ in connection.calls))

    async def test_apply_snapshots_and_only_updates_decomposition_once(self):
        connection = FakeConnection()
        original_formula = connection.sales["formula"]
        with TemporaryDirectory() as directory:
            report = await migrate(connection, apply=True, snapshot_dir=Path(directory))
            snapshot = json.loads(Path(report["snapshot"]).read_text())
            self.assertIsNone(snapshot["metric"]["decomposition"])
            self.assertEqual(snapshot["metric"]["formula"], original_formula)
            second = await migrate(connection, apply=True, snapshot_dir=Path(directory))
            self.assertFalse(second["metric_will_change"])
        self.assertEqual(connection.sales["version"], 4)
        updates = [sql for sql, _ in connection.calls if "UPDATE metric_info" in sql]
        self.assertEqual(len(updates), 1)
        self.assertNotIn("formula =", updates[0])
        self.assertEqual(connection.sales["formula"], original_formula)

    async def test_conflicting_manual_config_and_invalid_scope_not_overwritten(self):
        for connection in (FakeConnection(exists=True, config={"custom": True}),
                           FakeConnection(source=record("order_count", filters=[]))):
            with self.subTest(connection=connection), self.assertRaises(ValueError):
                await migrate(connection, apply=True)
            self.assertFalse(any("ALTER TABLE" in sql or "UPDATE metric_info" in sql for sql, _ in connection.calls))


if __name__ == "__main__":
    unittest.main()
