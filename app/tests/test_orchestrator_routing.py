import asyncio
import json
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from app.agent.orchestrator.graph import route_intent
from app.agent.orchestrator.nodes.intent_detection import intent_detection
from app.prompt.prompt_loader import load_prompt


def test_orchestrator_routes_four_intents():
    assert route_intent({"intent": "query_database"}) == "query_database"
    assert route_intent({"intent": "anomaly_analysis"}) == "anomaly_analysis"
    assert route_intent({"intent": "direct_answer"}) == "direct_answer"
    assert route_intent({"intent": "explain_knowledge"}) == "explain_knowledge"


def test_orchestrator_rejects_unknown_intent():
    with TestCase().assertRaises(ValueError):
        route_intent({"intent": "unknown"})


def test_intent_prompt_distinguishes_change_from_cause():
    prompt = load_prompt("orchestrator/intent_detection")
    assert "下降了多少？" in prompt
    assert "为什么北京地区7月销售额比6月下降？" in prompt
    assert "只问变化幅度，不问原因" in prompt


def test_intent_detection_returns_classification_and_observable_event():
    events = []
    runtime = SimpleNamespace(stream_writer=events.append)
    model_reply = AIMessage(content=json.dumps({
        "intent": "anomaly_analysis",
        "reason": "用户在追问下降原因",
        "keywords": ["北京", "销售额"],
    }, ensure_ascii=False))
    fake_llm = RunnableLambda(lambda _: model_reply)

    with patch(
        "app.agent.orchestrator.nodes.intent_detection.get_context_llm",
        return_value=fake_llm,
    ):
        result = asyncio.run(intent_detection(
            {"query": "为什么北京地区7月销售额比6月下降？"},
            runtime,
        ))

    assert result["intent"] == "anomaly_analysis"
    assert route_intent(result) == "anomaly_analysis"
    assert {"type": "intent_result", "intent": "anomaly_analysis",
            "reason": "用户在追问下降原因", "keywords": ["北京", "销售额"]} in events


def test_intent_detection_fails_closed_when_model_output_is_invalid():
    events = []
    runtime = SimpleNamespace(stream_writer=events.append)
    fake_llm = RunnableLambda(lambda _: AIMessage(content='{"intent":"unknown"}'))

    with patch(
        "app.agent.orchestrator.nodes.intent_detection.get_context_llm",
        return_value=fake_llm,
    ):
        with TestCase().assertRaises(ValueError):
            asyncio.run(intent_detection({"query": "看看销售额"}, runtime))

    assert not any(event.get("type") == "intent_result" for event in events)
