"""Offline A/B replay: real saved upstream and SQL rows, no external calls."""
import json
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from app.agent.anomaly_agent.Main_RootCause_Agent.diagnostic_runner import run_from_evidence
from app.agent.anomaly_agent.Main_RootCause_Agent.diagnosis import reason
from app.agent.anomaly_agent.Main_RootCause_Agent.diagnosis_support.nodes import prepare_evidence
from app.agent.anomaly_agent.Main_RootCause_Agent.diagnosis_support.shadow_audit import shadow_decision
from app.tests.diagnose_business_diagnosis_scoped_real import load_input


ROOT = Path(__file__).resolve().parents[6]


class RecordedQuery:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def run(self, request, runtime):
        self.calls.append((request.metric_key, request.period))
        row = self.rows[request.metric_key][request.period]
        return SimpleNamespace(success=True, rows=[{"value": row["value"]}], sql=row["sql"], error_message=None)


class RealEvidenceReplayTest(unittest.IsolatedAsyncioTestCase):
    async def test_next_member_after_first_scoped_result(self):
        source = ROOT / "diagnostics/rootcause_decomposition_c.json"
        data = load_input(source)
        evidence = prepare_evidence({"input": data})["evidence_items"]
        first = shadow_decision(data)["next_query_plan"][0]
        keys = ("dimension_column", "member_value", "parent_evidence_id", "task_id")
        state = {"observations": [{"action_name": "scoped_metric_decomposition", "status": "completed",
                                   "tool_arguments": {key: first[key] for key in keys}}]}
        llm = RunnableLambda(lambda _: AIMessage(content='{"action":"finish","reason":"结束"}'))
        with patch("app.agent.anomaly_agent.Main_RootCause_Agent.diagnosis.get_context_llm", return_value=llm):
            choice, _ = await reason(state, SimpleNamespace(context={}), data, evidence, remaining_sql=12)
        self.assertEqual(choice.action, "execute_tool")
        self.assertEqual(choice.tool_arguments["member_value"], "线上自营")

    async def test_same_upstream_evidence_a_and_b(self):
        source = ROOT / "diagnostics/rootcause_decomposition_c.json"
        archived = json.loads((ROOT / "diagnostics/business_diagnosis_scoped_real.json").read_text())
        scoped = next(item for item in archived["evidence"] if item["kind"] == "scoped_metric_decomposition")
        data = load_input(source)
        shadow = shadow_decision(data)
        self.assertEqual([item["member_value"] for item in shadow["next_query_plan"][:2]], ["线下旗舰店", "线上自营"])
        data.skill_strategy = replace(data.skill_strategy, max_query_budget=12)  # archived responses cover one member only
        recorded_query = RecordedQuery(scoped["payload"]["observations"])
        calls = {"llm": 0}

        def recorded_decision(_):
            calls["llm"] += 1
            return AIMessage(content=json.dumps({"action": "finish", "reason": "按现有证据结束"}, ensure_ascii=False))

        async def metric_by_key(_key):
            return SimpleNamespace(status="published", fact_table=data.metric_context.fact_table)

        context = {"meta_mysql_repository": SimpleNamespace(get_metric_by_key=metric_by_key), "test": True}
        with patch("app.agent.anomaly_agent.Main_RootCause_Agent.diagnosis.get_context_llm", return_value=RunnableLambda(recorded_decision)), patch("app.agent.anomaly_agent.Main_RootCause_Agent.diagnosis_support.tools.DataQueryTool", return_value=recorded_query):
            state = await run_from_evidence(data, SimpleNamespace(context=context, stream_writer=lambda _event: None))
        self.assertEqual(calls["llm"], 2, state["main_state"]["observations"][-2:])
        self.assertEqual(len(recorded_query.calls), 4)
        self.assertEqual(state["result"].total_query_count, 4)
        new = [item for item in state["evidence_items"] if item.kind == "scoped_metric_decomposition"]
        self.assertEqual(len(new), 1)
        self.assertTrue(new[0].reconciled)
        self.assertEqual(new[0].payload["member_value"], "线下旗舰店")
        self.assertEqual(shadow["sql_executed"], 0)


if __name__ == "__main__":
    unittest.main()
