"""Opt-in diagnostic telemetry. Never changes graph state or business decisions."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from langchain_core.callbacks import AsyncCallbackHandler


def enabled() -> bool:
    return os.getenv("BUSINESS_DIAGNOSIS_AUDIT", "0") == "1"


def emit(event: dict[str, Any]) -> None:
    if not enabled():
        return
    path = os.getenv("BUSINESS_DIAGNOSIS_AUDIT_PATH")
    if not path:
        return
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
    except OSError:
        # Telemetry must not break the analysis.
        pass


class LLMUsage(AsyncCallbackHandler):
    def __init__(self, model: str | None = None):
        self.starts: dict[str, float] = {}
        self.model = model

    async def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        self.starts[str(run_id)] = time.monotonic()

    async def on_llm_end(self, response, *, run_id, **kwargs):
        generations = response.generations or []
        message = generations[0][0].message if generations and generations[0] else None
        usage = getattr(message, "usage_metadata", None) or (response.llm_output or {}).get("token_usage") or {}
        metadata = getattr(message, "response_metadata", None) or {}
        input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
        output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
        cost = None
        try:
            if input_tokens is not None and output_tokens is not None:
                input_rate = float(os.environ["BUSINESS_DIAGNOSIS_INPUT_USD_PER_1M"])
                output_rate = float(os.environ["BUSINESS_DIAGNOSIS_OUTPUT_USD_PER_1M"])
                cost = round((input_tokens * input_rate + output_tokens * output_rate) / 1_000_000, 8)
        except (KeyError, ValueError):
            pass
        emit({"kind": "llm", "node": "main_reason", "duration_ms": round((time.monotonic() - self.starts.pop(str(run_id), time.monotonic())) * 1000, 2),
              "model": metadata.get("model_name") or metadata.get("model") or (response.llm_output or {}).get("model_name") or self.model,
              "input_tokens": input_tokens, "output_tokens": output_tokens,
              "estimated_cost_usd": cost, "cost_reason": None if cost is not None else "missing_usage_or_configured_rates"})

    async def on_llm_error(self, error, *, run_id, **kwargs):
        emit({"kind": "llm_error", "node": "main_reason", "model": self.model,
              "duration_ms": round((time.monotonic() - self.starts.pop(str(run_id), time.monotonic())) * 1000, 2),
              "input_tokens": None, "output_tokens": None, "estimated_cost_usd": None, "error_type": type(error).__name__})


