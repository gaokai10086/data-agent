import json
import unittest
from decimal import Decimal
from unittest.mock import patch

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from app.agent.anomaly_agent.BusinessDiagnosis_Agent.graph import business_diagnosis_graph
from app.agent.anomaly_agent.BusinessDiagnosis_Agent.schemas import evidence_id
from app.agent.anomaly_agent.BusinessDiagnosis_Agent.tests.test_skeleton import make_business_input
from app.agent.anomaly_agent.BusinessDiagnosis_Agent.tools import DIAGNOSIS_TOOL_REGISTRY
from app.agent.anomaly_agent.MetricDecomposition_Agent.schemas import DecompositionComponent, DecompositionReconciliation


class FakeScopedTool:
    async def run(self, input_data, runtime, *, tool_arguments, query_budget_remaining):
        return {
            "scope": [{"dimension_column": tool_arguments["dimension_column"], "member_value": tool_arguments["member_value"]}],
            "member_value": tool_arguments["member_value"], "dimension_column": tool_arguments["dimension_column"],
            "query_count": 4, "components": [DecompositionComponent(
                factor_key="order_count", factor_name="订单数量", comparison_value=Decimal("60"), current_value=Decimal("30"),
                change_amount=Decimal("-30"), contribution_amount=Decimal("-40000"), contribution_rate=Decimal("0.5"),
            )],
            "reconciliation": DecompositionReconciliation(
                overall_comparison_value=Decimal("121726.61"), overall_current_value=Decimal("34723.06"),
                overall_change_amount=Decimal("-87003.55"), summed_contribution_amount=Decimal("-87003.55"),
                matches=True, tolerance=Decimal("0.01"),
            ),
            "observations": {},
        }


class ReactLoopTest(unittest.IsolatedAsyncioTestCase):
    async def test_tool_observation_returns_to_main_reason(self):
        input_data = make_business_input()
        input_data.available_tool_names = ("scoped_metric_decomposition",)
        path = input_data.attribution_result.paths[0]
        parent_id = evidence_id("dimension_attribution", path.model_dump(mode="json"))
        calls = {"count": 0}

        def fake_llm(_):
            calls["count"] += 1
            if calls["count"] == 1:
                response = {"action": "execute_tool", "tool_name": "scoped_metric_decomposition", "tool_arguments": {
                    "dimension_column": "dim_store.channel_type", "member_value": "线下旗舰店", "parent_evidence_id": parent_id,
                }, "reason": "验证最大渠道的订单量与客单价"}
            else:
                response = {"action": "finish", "reason": "范围证据已返回", "findings": [], "hypotheses": [{
                    "hypothesis_id": "h1", "statement": "范围内订单量因素有统计贡献，尚非业务因果。", "status": "supported",
                    "supporting_evidence_ids": [], "validation_metrics": [], "executable": True,
                }]}
            return AIMessage(content=json.dumps(response, ensure_ascii=False))

        state = {}
        with patch("app.agent.anomaly_agent.BusinessDiagnosis_Agent.nodes.get_context_llm", return_value=RunnableLambda(fake_llm)), patch.dict(DIAGNOSIS_TOOL_REGISTRY, {"scoped_metric_decomposition": FakeScopedTool()}):
            async for mode, chunk in business_diagnosis_graph.astream({"input": input_data}, context={"test": True}, stream_mode=["updates", "values"]):
                if mode == "values" and isinstance(chunk, dict):
                    state.update(chunk)
        self.assertEqual(calls["count"], 2)
        self.assertEqual(state["query_count"], 4)
        self.assertEqual(state["result"].completion_status, "complete")
        self.assertEqual(state["result"].total_query_count, 4)
        self.assertTrue(any(item.kind == "scoped_metric_decomposition" for item in state["evidence_items"]))


if __name__ == "__main__":
    unittest.main()
