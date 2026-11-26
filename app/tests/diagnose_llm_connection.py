"""诊断 LLM 连接，不执行主 Agent 或数据库查询。

执行：
    .venv/bin/python -m app.tests.diagnose_llm_connection

输出：diagnostics/llm_connection_diagnostic.json
不会输出 API Key，只记录脱敏后的配置和错误类型。
"""
from __future__ import annotations

import asyncio
import json
import socket
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.config.app_config import DEFAULT_LLM_MODEL, DEFAULT_LLM_PROVIDER, app_config
from app.core.llm import get_llm


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "diagnostics" / "llm_connection_diagnostic.json"


def redact(value: str | None) -> str:
    if not value:
        return "missing"
    return value[:4] + "..." + value[-4:]


def host_check(url: str) -> dict:
    parsed = urlparse(url)
    result = {"url": url, "host": parsed.hostname, "port": parsed.port or 443}
    started = time.monotonic()
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
        result["dns"] = "ok"
        result["addresses"] = sorted({item[4][0] for item in addresses})
    except Exception as exc:
        result["dns"] = "failed"
        result["error_type"] = type(exc).__name__
        result["error"] = str(exc)
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000, 1)
    return result


async def main() -> int:
    config = app_config.llm
    result: dict = {
        "provider": DEFAULT_LLM_PROVIDER,
        "model_tier": DEFAULT_LLM_MODEL,
        "base_url": config.base_url,
        "api_key": redact(config.api_key),
        "checks": {},
    }

    result["checks"]["config"] = {
        "status": "ok" if config.api_key and config.base_url else "failed",
        "base_url": config.base_url,
        "api_key_present": bool(config.api_key),
    }
    result["checks"]["dns"] = host_check(config.base_url)

    # Check HTTPS/API reachability without printing response bodies or secrets.
    parsed = urlparse(config.base_url)
    models_url = config.base_url.rstrip("/") + "/models"
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=15.0, trust_env=True) as client:
            response = await client.get(models_url, headers={"Authorization": f"Bearer {config.api_key}"})
        result["checks"]["https"] = {
            "status": "ok" if response.status_code < 500 else "failed",
            "http_status": response.status_code,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
            "url": f"{parsed.scheme}://{parsed.hostname}/models",
        }
    except Exception as exc:
        result["checks"]["https"] = {
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
        }

    started = time.monotonic()
    try:
        llm = get_llm(provider=DEFAULT_LLM_PROVIDER, model_tier=DEFAULT_LLM_MODEL, max_retries=0)
        response = await llm.ainvoke("请只返回 ok")
        result["checks"]["chat_completion"] = {
            "status": "ok",
            "content_preview": str(response.content)[:80],
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
        }
    except Exception as exc:
        result["checks"]["chat_completion"] = {
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
        }

    passed = result["checks"]["chat_completion"]["status"] == "ok"
    result["status"] = "passed" if passed else "failed"
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"saved: {OUTPUT}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
