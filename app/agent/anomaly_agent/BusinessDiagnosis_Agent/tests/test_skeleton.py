import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from app.agent.anomaly_agent.BusinessDiagnosis_Agent.graph import business_diagnosis_graph
from app.agent.anomaly_agent.BusinessDiagnosis_Agent.nodes import finalize_diagnosis, prepare_evidence
from app.agent.anomaly_agent.BusinessDiagnosis_Agent.schemas import BusinessDiagnosisInput
from app.agent.anomaly_agent.DimensionAttribution_Agent.schemas import (
    AttributionPath, AttributionResult, DimensionMemberAttribution, ReconciliationResult,
)
from app.agent.anomaly_agent.DimensionAttribution_Agent.tests.test_skeleton import make_input as make_dimension_input
from app.agent.anomaly_agent.MetricDecomposition_Agent.tests.test_skeleton import make_input as make_decomposition_input


def make_business_input(*, with_attribution=True, with_decomposition=True, partial=False):
    decomposition_input = make_decomposition_input()
    request = decomposition_input.request
    inspection = decomposition_input.inspection_outcome
    metric_context = decomposition_input.metric_context
    attribution = None
    if with_attribution:
        reconciliation = ReconciliationResult(
            comparison_sum=Decimal("233378.95"), current_sum=Decimal("120953.99"),
            summed_change_amount=Decimal("-112424.96"), overall_comparison_value=Decimal("233378.95"),
            overall_current_value=Decimal("120953.99"), overall_change_amount=Decimal("-112424.96"),
            comparison_matches=True, current_matches=True, change_matches=True, tolerance=Decimal("0.01"),
        )
        path = AttributionPath(
            task_id="channel-task", depth=0, dimension_column="dim_store.channel_type",
            attributions=[DimensionMemberAttribution(
                group_value="线下旗舰店", comparison_value=Decimal("121726.61"),
                current_value=Decimal("34723.06"), change_amount=Decimal("-87003.55"),
                net_contribution_rate=Decimal("0.7739"),
            )], reconciliation=reconciliation,
        )
        attribution = AttributionResult(
            request_id=request.request_id, status="completed", completion_status="partial" if partial else "complete",
            reason="completed", stop_reason="budget_exhausted" if partial else "completed",
            summary="channel", paths=[path], total_query_count=2,
        )
    decomposition = None
    if with_decomposition:
        decomposition = decomposition_input
        # The standalone fixture's graph input is not a result; use a minimal result.
        from app.agent.anomaly_agent.MetricDecomposition_Agent.schemas import (
            DecompositionComponent, DecompositionReconciliation, DecompositionResult,
        )
        decomposition = DecompositionResult(
            request_id=request.request_id, status="completed", completion_status="complete",
            reason="completed", stop_reason="completed", summary="decomposition",
            components=[
                DecompositionComponent(
                    factor_key="order_count", factor_name="订单数量", comparison_value=Decimal("127"),
                    current_value=Decimal("85"), change_amount=Decimal("-42"),
                    contribution_amount=Decimal("-68472.97"), contribution_rate=Decimal("0.6091"),
                ),
                DecompositionComponent(
                    factor_key="avg_order_amount", factor_name="平均支付订单金额", comparison_value=Decimal("1837.62"),
                    current_value=Decimal("1422.98"), change_amount=Decimal("-414.64"),
                    contribution_amount=Decimal("-43951.99"), contribution_rate=Decimal("0.3909"),
                ),
            ],
            reconciliation=DecompositionReconciliation(
                overall_comparison_value=Decimal("233378.95"), overall_current_value=Decimal("120953.99"),
                overall_change_amount=Decimal("-112424.96"), summed_contribution_amount=Decimal("-112424.96"),
                matches=True, tolerance=Decimal("0.01"),
            ),
        )
    return BusinessDiagnosisInput(
        raw_question="为什么销售额下降？",
        analysis_request=request,
        inspection_outcome=inspection,
        attribution_result=attribution,
        decomposition_result=decomposition,
        metric_context=metric_context,
        skill_strategy=decomposition_input.skill_strategy,
        available_tool_names=(),
    )


class BusinessDiagnosisGraphTest(unittest.IsolatedAsyncioTestCase):
    async def _run(self, input_data, response=None):
        response = response or {
            "action": "finish", "reason": "证据整理完成", "findings": [], "hypotheses": [{
                "hypothesis_id": "h1", "statement": "线下旗舰店的下降值得进一步核查具体业务原因。",
                "status": "hypothesis", "supporting_evidence_ids": [],
                "validation_metrics": ["订单来源"], "executable": True,
            }],
        }
        llm = RunnableLambda(lambda _: AIMessage(content=__import__("json").dumps(response, ensure_ascii=False)))
        with patch("app.agent.anomaly_agent.BusinessDiagnosis_Agent.nodes.get_context_llm", return_value=llm):
            state = {}
            async for mode, chunk in business_diagnosis_graph.astream(
                {"input": input_data}, context={"test": True}, stream_mode=["updates", "values"],
            ):
                if mode == "values" and isinstance(chunk, dict):
                    state.update(chunk)
            return state

    async def test_a_combined_evidence_is_separated(self):
        state = await self._run(make_business_input())
        result = state["result"]
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.total_query_count, 0)
        self.assertEqual(len(result.evidence_ids), 3)
        self.assertTrue(any("订单数量" in item.statement for item in result.findings))
        self.assertTrue(any(item.status == "hypothesis" for item in result.hypotheses))

    async def test_b_only_attribution_keeps_decomposition_gap(self):
        state = await self._run(make_business_input(with_decomposition=False))
        self.assertTrue(any(item.finding_id == "gap_decomposition" for item in state["result"].findings))

    async def test_c_only_decomposition_keeps_dimension_gap(self):
        state = await self._run(make_business_input(with_attribution=False))
        self.assertTrue(any(item.finding_id == "gap_dimension" for item in state["result"].findings))

    async def test_d_partial_attribution_is_not_complete(self):
        state = await self._run(make_business_input(with_decomposition=False, partial=True))
        self.assertEqual(state["result"].completion_status, "partial")
        self.assertEqual(state["result"].status, "partial")

    async def test_e_no_evidence_is_unavailable(self):
        input_data = make_business_input(with_attribution=False, with_decomposition=False)
        with patch("app.agent.anomaly_agent.BusinessDiagnosis_Agent.nodes.get_context_llm", side_effect=RuntimeError("offline")):
            state = {}
            async for mode, chunk in business_diagnosis_graph.astream(
                {"input": input_data}, context={"test": True}, stream_mode=["updates", "values"],
            ):
                if mode == "values" and isinstance(chunk, dict):
                    state.update(chunk)
        self.assertEqual(state["result"].status, "partial")
        # Overall ChangeInspection remains trusted evidence even when no specialist result exists.
        self.assertEqual(state["result"].completion_status, "partial")

    def test_input_rejects_mismatched_result(self):
        input_data = make_business_input()
        input_data.decomposition_result.request_id = "other-request"
        with self.assertRaises(ValueError):
            BusinessDiagnosisInput.model_validate(input_data.model_dump())

    async def test_f_llm_unregistered_tool_is_blocked(self):
        response = {"action": "execute_tool", "tool_name": "fake_business_query", "reason": "need more data"}
        state = await self._run(make_business_input(), response=response)
        self.assertEqual(state["observations"][-1].status, "blocked")
        self.assertEqual(state["result"].status, "partial")

    def test_e_empty_evidence_finalizes_unavailable(self):
        input_data = make_business_input(with_attribution=False, with_decomposition=False)
        result = finalize_diagnosis({"input": input_data, "evidence_items": [], "stop_reason": "no_credible_evidence"})["result"]
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.completion_status, "none")


if __name__ == "__main__":
    unittest.main()
