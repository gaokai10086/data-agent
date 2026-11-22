"""运行原始异动问题，仅导出主 Agent 的结构化结果，不生成报告或图表。

执行：python -m app.tests.export_main_rootcause_structured_output
需要本地 MySQL、检索服务和模型服务可用；失败会保存失败状态，不伪造结果。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.tests.diagnose_rootcause_business_diagnosis_integration import run


OUTPUT = Path(__file__).resolve().parents[2] / "diagnostics/main_rootcause_structured_output.json"


async def main() -> int:
    outcome = await run("D")
    if outcome.get("status") != "passed" or not outcome.get("report_input"):
        payload = {
            "question": outcome.get("question"), "status": "failed",
            "error_type": outcome.get("exception_type"), "error": outcome.get("error"),
            "completed_nodes": outcome.get("nodes") or [],
        }
        exit_code = 1
    else:
        report_input = outcome["report_input"]
        payload = {"question": outcome["question"],
                   "status": report_input["analysis_result"]["completion_status"],
                   **report_input}
        exit_code = 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(OUTPUT)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
