import json
import importlib
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from app.agent.anomaly_agent.Main_RootCause_Agent.diagnosis_support.tests.test_skeleton import make_business_input
from app.agent.anomaly_agent.Main_RootCause_Agent.actions.registry import ACTION_REGISTRY, ActionSpec, AttributionExecution, DecompositionExecution
from app.agent.anomaly_agent.Main_RootCause_Agent.analysis_result import build_analysis_result, materialize_stage, materialize_report_input
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.action_executor import action_executor
from app.agent.anomaly_agent.Main_RootCause_Agent.nodes.supervisor_reason import supervisor_reason
from app.agent.anomaly_agent.Main_RootCause_Agent.tests.test_dimension_attribution_action import attribution_state
from app.agent.anomaly_agent.MetricDecomposition_Agent.schemas import DecompositionReconciliation
from app.tests.test_supervisor_react import runtime


ROOT = Path(__file__).resolve().parents[5]


class DiagnosisMigrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_full_main_graph_combines_all_actions_without_diagnosis_subgraph(self):
        root_module = importlib.import_module("app.agent.anomaly_agent.Main_RootCause_Agent.graph")
        fixture = make_business_input()
        archived = json.loads((ROOT / "diagnostics/business_diagnosis_scoped_real.json").read_text())
        payload = next(item["payload"] for item in archived["evidence"] if item["kind"] == "scoped_metric_decomposition")
        payload["reconciliation"] = DecompositionReconciliation.model_validate(payload["reconciliation"])

        async def extract(_state, runtime):
            return {"extracted_metrics": ["销售额"]}

        async def schema(_state, runtime):
            return {"schema_result": {"status": "matched", "task_type": "hypothesis_analysis"}}

        async def parse(_state, runtime):
            return {"parsed_result": {"status": "passed", "parsed_request": {
                "metric": "销售额", "target_period": {"year": 2026, "month": 8},
                "comparison_period": {"year": 2026, "month": 7}, "filters": [], "expected_direction": "decrease"}}}

        change = AsyncMock(return_value=fixture.inspection_outcome)
        dimension = AsyncMock(return_value=AttributionExecution(fixture.attribution_result, ["finalize_attribution"]))
        decomposition = AsyncMock(return_value=DecompositionExecution(fixture.decomposition_result, ["finalize_decomposition"]))
        scoped = AsyncMock(return_value=payload)
        specs = {
            "change_inspection": ActionSpec("change_inspection", "change_inspection", "period_comparison", change),
            "dimension_attribution": ActionSpec("dimension_attribution", "dimension_attribution", "dimension_attribution", dimension),
            "metric_decomposition": ActionSpec("metric_decomposition", "metric_decomposition", "metric_decomposition", decomposition),
            "scoped_metric_decomposition": ActionSpec("scoped_metric_decomposition", "business_diagnosis", "hypothesis_analysis", scoped),
        }
        llm = RunnableLambda(lambda _: AIMessage(content=json.dumps({"action": "finish", "reason": "证据审核完成", "hypotheses": []})))
        with patch.object(root_module, "extract_metrics", extract), patch.object(root_module, "select_task_schema", schema), patch.object(root_module, "parse_task_request", parse), patch.dict(ACTION_REGISTRY, specs), patch("app.agent.anomaly_agent.Main_RootCause_Agent.diagnosis.get_context_llm", return_value=llm):
            graph = root_module.build_root_cause_graph()
            state = await graph.ainvoke({"raw_question": "分析渠道贡献、订单量客单价，并进一步诊断下降因素", "sql_budget_limit": 8}, context={"test": True})
        names = [item["action_name"] for item in state["observations"]]
        self.assertEqual(names, ["change_inspection", "dimension_attribution", "metric_decomposition", "scoped_metric_decomposition"])
        self.assertEqual(state["analysis_result"]["completion_status"], "complete")
        self.assertEqual(state["analysis_result"]["sql_usage"]["total"], 8)
        self.assertTrue(state["analysis_result"]["dimension_paths"])
        handoff = materialize_report_input(state)
        self.assertEqual(len(handoff["dimension_attribution"]), 1)
        self.assertEqual(len(handoff["scoped_evidence"]), 1)
        self.assertEqual(scoped.await_count, 1)

    async def test_same_supervisor_runs_scoped_tool_then_finishes(self):
        fixture = make_business_input()
        state = attribution_state(task_type="hypothesis_analysis")
        state["raw_question"] = "分析渠道贡献和订单量客单价，并进一步诊断下降因素"
        attribution = fixture.attribution_result.model_dump(mode="json")
        attribution["total_query_count"] = 18  # leave four queries from the explicitly supplied test budget
        state["observations"].append({"step": 2, "action_name": "dimension_attribution", "status": "completed",
                                      "completion_status": "complete", "attribution_result": attribution,
                                      "evidence": {"total_query_count": 18}})
        state["observations"].append({"step": 3, "action_name": "metric_decomposition", "status": "completed",
                                      "completion_status": "complete", "decomposition_result": fixture.decomposition_result.model_dump(mode="json"),
                                      "evidence": {"total_query_count": 0}})
        archived = json.loads((ROOT / "diagnostics/business_diagnosis_scoped_real.json").read_text())
        payload = next(item["payload"] for item in archived["evidence"] if item["kind"] == "scoped_metric_decomposition")
        payload["reconciliation"] = DecompositionReconciliation.model_validate(payload["reconciliation"])
        executor = AsyncMock(return_value=payload)
        spec = ActionSpec("scoped_metric_decomposition", "business_diagnosis", "hypothesis_analysis", executor)
        llm = RunnableLambda(lambda _: AIMessage(content=json.dumps({"action": "finish", "reason": "已阅读证据", "hypotheses": []})))
        with patch("app.agent.anomaly_agent.Main_RootCause_Agent.diagnosis.get_context_llm", return_value=llm), patch.dict(ACTION_REGISTRY, {"scoped_metric_decomposition": spec}):
            first = await supervisor_reason(state, runtime())
            self.assertEqual(first["supervisor_decision"]["tool_name"], "scoped_metric_decomposition")
            state.update(first)
            executed = await action_executor(state, runtime())
            self.assertEqual(executed["observations"][-1]["status"], "completed")
            self.assertEqual(executed["sql_usage"]["total"], 24)
            state.update(executed)
            second = await supervisor_reason(state, runtime())
        self.assertEqual(second["supervisor_decision"]["action"], "finish")
        self.assertEqual(second["diagnosis_result"]["completion_status"], "complete")
        state.update(second)
        result = build_analysis_result(state)
        self.assertEqual(result.completion_status, "complete")
        self.assertTrue(result.dimension_paths)
        self.assertTrue(materialize_stage(state, result, "dimension_attribution"))
        self.assertTrue(result.evidence_ids)


if __name__ == "__main__":
    unittest.main()
