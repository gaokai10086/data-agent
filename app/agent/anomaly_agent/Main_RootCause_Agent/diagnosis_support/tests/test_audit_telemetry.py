import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from app.agent.anomaly_agent.Main_RootCause_Agent.diagnosis_support.audit import LLMUsage


class AuditTelemetryTest(unittest.TestCase):
    def test_usage_and_configured_cost_are_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            with patch.dict("os.environ", {
                "BUSINESS_DIAGNOSIS_AUDIT": "1",
                "BUSINESS_DIAGNOSIS_AUDIT_PATH": str(path),
                "BUSINESS_DIAGNOSIS_INPUT_USD_PER_1M": "1",
                "BUSINESS_DIAGNOSIS_OUTPUT_USD_PER_1M": "2",
            }):
                callback = LLMUsage("test-model")
                run_id = uuid4()
                message = AIMessage(content="ok", usage_metadata={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150})
                result = LLMResult(generations=[[ChatGeneration(message=message)]])

                async def exercise():
                    await callback.on_chat_model_start({}, [], run_id=run_id)
                    await callback.on_llm_end(result, run_id=run_id)

                asyncio.run(exercise())
            event = json.loads(path.read_text().strip())
            self.assertEqual(event["model"], "test-model")
            self.assertEqual((event["input_tokens"], event["output_tokens"]), (100, 50))
            self.assertEqual(event["estimated_cost_usd"], 0.0002)


if __name__ == "__main__":
    unittest.main()
