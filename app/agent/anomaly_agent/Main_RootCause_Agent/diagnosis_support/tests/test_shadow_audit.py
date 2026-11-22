import unittest
from decimal import Decimal

from app.agent.anomaly_agent.Main_RootCause_Agent.diagnosis_support.shadow_audit import shadow_decision
from app.agent.anomaly_agent.Main_RootCause_Agent.diagnosis_support.tests.test_skeleton import make_business_input
from app.agent.anomaly_agent.DimensionAttribution_Agent.schemas import DimensionMemberAttribution


class ShadowAuditTest(unittest.TestCase):
    def test_multi_member_needed_for_eighty_percent(self):
        data = make_business_input()
        path = data.attribution_result.paths[0]
        path.attributions = [
            DimensionMemberAttribution(group_value="A", comparison_value=Decimal(70), current_value=Decimal(0), change_amount=Decimal(-70), net_contribution_rate=Decimal("0.70")),
            DimensionMemberAttribution(group_value="B", comparison_value=Decimal(20), current_value=Decimal(0), change_amount=Decimal(-20), net_contribution_rate=Decimal("0.20")),
            DimensionMemberAttribution(group_value="C", comparison_value=Decimal(10), current_value=Decimal(0), change_amount=Decimal(-10), net_contribution_rate=Decimal("0.10")),
        ]
        result = shadow_decision(data)
        self.assertEqual([item["member_value"] for item in result["next_query_plan"]], ["A", "B"])
        self.assertEqual(result["next_query_plan"][-1]["gross_decline_coverage"], "0.9")
        self.assertEqual(result["sql_executed"], 0)

    def test_real_record_needs_second_member(self):
        from pathlib import Path
        from app.tests.diagnose_business_diagnosis_scoped_real import load_input
        source = Path(__file__).resolve().parents[5] / "diagnostics" / "rootcause_decomposition_c.json"
        data = load_input(source)
        result = shadow_decision(data)
        self.assertEqual([item["member_value"] for item in result["next_query_plan"][:2]], ["线下旗舰店", "线上自营"])
