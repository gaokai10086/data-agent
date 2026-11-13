"""Synthetic relational fixtures for grouping semantics; never contacts services."""

import sqlite3
import unittest
from decimal import Decimal

from app.agent.anomaly_agent.DimensionAttribution_Agent.tests.test_skeleton import make_input
from app.agent.anomaly_agent.DimensionAttribution_Agent.tools import DimensionPeriodComparisonTool
from app.agent.anomaly_agent.schemas import FilterCondition, MetricDimension


class AttributionSQLSemanticsTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE dim_store (
                store_id TEXT PRIMARY KEY, city TEXT, channel_type TEXT
            );
            CREATE TABLE fact_order (
                order_sn TEXT PRIMARY KEY, store_id TEXT, order_status TEXT,
                pay_time TEXT, pay_amount NUMERIC
            );
            INSERT INTO dim_store VALUES
                ('s1','北京','线上'), ('s2','北京','线下'),
                ('s3','北京','无订单'), ('s4','上海','线上');
            INSERT INTO fact_order VALUES
                ('j1','s1','已完成','2026-07-08 00:00:00',10),
                ('j2','s2','已完成','2026-07-09 00:00:00',NULL),
                ('j3','s2','已取消','2026-07-10 00:00:00',99),
                ('a1','s1','已完成','2026-08-08 00:00:00',8),
                ('a2','s2','已完成','2026-08-09 00:00:00',5),
                ('a3','s4','已完成','2026-08-10 00:00:00',100);
        """)
        self.tool = DimensionPeriodComparisonTool()

    def tearDown(self):
        self.db.close()

    def grouped_sql(self, period):
        end = "2026-08-01" if period == "2026-07" else "2026-09-01"
        return f"""
            SELECT ds.channel_type AS dimension_value, SUM(fo.pay_amount) AS value
            FROM dim_store ds LEFT JOIN fact_order fo
              ON fo.store_id=ds.store_id AND fo.order_status='已完成'
             AND fo.pay_time>='{period}-01' AND fo.pay_time<'{end}'
            WHERE ds.city='北京' GROUP BY ds.channel_type
        """

    def baseline_sql(self, period):
        end = "2026-08-01" if period == "2026-07" else "2026-09-01"
        return f"""
            SELECT ds.channel_type AS dimension_value, SUM(fo.pay_amount) AS value
            FROM fact_order fo JOIN dim_store ds ON ds.store_id=fo.store_id
            WHERE ds.city='北京' AND fo.order_status='已完成'
              AND fo.pay_time>='{period}-01' AND fo.pay_time<'{end}'
            GROUP BY ds.channel_type
        """

    def rows(self, sql):
        return [dict(row) for row in self.db.execute(sql)]

    def test_both_periods_match_fact_first_baseline_with_zero_members(self):
        for period in ("2026-07", "2026-08"):
            with self.subTest(period=period):
                actual = self.tool._normalize_rows(self.rows(self.grouped_sql(period)), period)
                baseline = self.tool._normalize_rows(self.rows(self.baseline_sql(period)), period)
                self.assertEqual(actual["无订单"], Decimal("0"))
                self.assertEqual(actual.get("线下"), baseline.get("线下"))
                self.assertEqual(actual.get("线上"), baseline.get("线上"))
                self.assertEqual(sum(actual.values()), sum(baseline.values()))
        july = self.tool._normalize_rows(self.rows(self.grouped_sql("2026-07")), "2026-07")
        self.assertEqual(july["线下"], Decimal("0"))  # SUM over NULL pay_amount

    def test_duplicate_dimension_key_is_rejected_by_real_schema_shape(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO dim_store VALUES ('s1','北京','线上')")

    def test_missing_nonzero_group_fails_reconciliation(self):
        august = self.tool._normalize_rows(self.rows(self.grouped_sql("2026-08")), "2026-08")
        july = self.tool._normalize_rows(self.rows(self.grouped_sql("2026-07")), "2026-07")
        august.pop("线下")
        overall = type("Overall", (), {
            "current_value": Decimal("13"), "comparison_value": Decimal("10"),
            "change_amount": Decimal("3"),
        })()
        with self.assertRaisesRegex(ValueError, "未对账"):
            self.tool.compute_attributions(august, july, overall)

    def test_limit_is_rejected_even_when_omitted_group_has_zero_value(self):
        input_data = make_input()
        input_data.request.filters = [FilterCondition(dimension="城市", value="北京")]
        dimension = MetricDimension(name="渠道", column="dim_store.channel_type")
        sql = self.grouped_sql("2026-07") + " LIMIT 1"
        with self.assertRaisesRegex(ValueError, "LIMIT"):
            self.tool._validate_sql_contract(
                sql, input_data.request, input_data.metric_context,
                dimension, "2026-07", [],
            )
