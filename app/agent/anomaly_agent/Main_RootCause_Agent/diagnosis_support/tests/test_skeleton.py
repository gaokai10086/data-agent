import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from app.agent.anomaly_agent.Main_RootCause_Agent.diagnosis_support.nodes import finalize_diagnosis, prepare_evidence
from app.agent.anomaly_agent.Main_RootCause_Agent.diagnosis_support.schemas import BusinessDiagnosisInput
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


class BusinessEvidenceTest(unittest.TestCase):
    def _result(self, data):
        evidence = prepare_evidence({"input": data})["evidence_items"]
        return finalize_diagnosis({"input": data, "evidence_items": evidence, "hypotheses": [], "observations": []})["result"]

    def test_combined_evidence_is_separated(self):
        result = self._result(make_business_input())
        self.assertEqual(len(result.evidence_ids), 3)
        self.assertTrue(any("订单数量" in item.statement for item in result.findings))

    def test_missing_specialist_results_show_gaps(self):
        without_decomposition = self._result(make_business_input(with_decomposition=False))
        self.assertTrue(any(item.finding_id == "gap_decomposition" for item in without_decomposition.findings))
        without_attribution = self._result(make_business_input(with_attribution=False))
        self.assertTrue(any(item.finding_id == "gap_dimension" for item in without_attribution.findings))

    def test_partial_attribution_stays_partial(self):
        result = self._result(make_business_input(with_decomposition=False, partial=True))
        self.assertEqual(result.completion_status, "partial")

    def test_mismatched_request_rejected(self):
        data = make_business_input()
        data.decomposition_result.request_id = "other-request"
        with self.assertRaises(ValueError):
            BusinessDiagnosisInput.model_validate(data.model_dump())

    def test_empty_evidence_is_unavailable(self):
        data = make_business_input(with_attribution=False, with_decomposition=False)
        result = finalize_diagnosis({"input": data, "evidence_items": [], "stop_reason": "no_credible_evidence"})["result"]
        self.assertEqual(result.status, "unavailable")


if __name__ == "__main__":
    unittest.main()
