"""SalesAmountAnomalySkill 策略契约测试。"""
import unittest

from app.agent.anomaly_agent.Main_RootCause_Agent.actions import ACTION_REGISTRY
from app.agent.anomaly_agent.Main_RootCause_Agent.skills.registry import (
    AVAILABLE_CAPABILITIES,
    load_skill,
    resolve_skill_capabilities,
    validate_skill_strategy,
)


class SalesAmountSkillStrategyTest(unittest.TestCase):
    def setUp(self):
        self.skill = load_skill("sales_amount_anomaly")

    def test_skill_loads_markdown_and_typed_strategy(self):
        instructions = self.skill.load_instructions()
        self.assertIn("ChangeInspection", instructions)
        self.assertIn("DimensionAttributionAgent", instructions)
        self.assertTrue(self.skill.strategy.change_inspection_required)
        self.assertEqual(self.skill.strategy.dimension_source, "metric_context.dimensions")
        self.assertEqual(self.skill.strategy.max_query_budget, 8)

    def test_allowed_capabilities_are_subset_of_real_capabilities(self):
        resolved = resolve_skill_capabilities(self.skill)
        self.assertEqual(resolved["unavailable_capabilities"], [])
        self.assertEqual(resolved["available_capabilities"], ["change_inspection"])
        self.assertNotIn("dimension_group_query", ACTION_REGISTRY)
        self.assertNotIn("contribution_calculation", ACTION_REGISTRY)
        self.assertIn("change_inspection", AVAILABLE_CAPABILITIES)

    def test_strategy_contract_is_valid(self):
        validate_skill_strategy(self.skill)
        self.assertEqual(
            [item.name for item in self.skill.strategy.tool_contracts],
            [
                "dimension_group_query",
                "dimension_period_comparison",
                "contribution_calculation",
                "reconciliation_check",
                "drilldown_validation",
            ],
        )


if __name__ == "__main__":
    unittest.main()
