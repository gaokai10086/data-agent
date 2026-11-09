"""Orchestrator 与异动归因子 Agent 之间的适配层。

意图识别和分流已经在 Orchestrator 完成；本文件不再次分类，也不解析 SQL。
它只转换输入 State、调用子 Graph、转发可供前端消费的事件。
"""

from __future__ import annotations

from langgraph.runtime import Runtime

from app.agent.anomaly_agent.Main_RootCause_Agent.graph import main_root_cause_agent
from app.agent.anomaly_agent.schemas import PreparationResult
from app.agent.data_agent.context import DataAgentContext
from app.agent.orchestrator.state import OrchestratorState
from app.core.logging import logger


TECHNICAL_FAILURE_MESSAGE = "异动分析暂时无法完成，请稍后重试。"


def _technical_failure(
    runtime: Runtime[DataAgentContext],
    reason: str,
    *,
    request_id: str | None = None,
) -> dict[str, object]:
    """返回结构化技术失败，并且只发送一次用户可见的最终错误提示。"""
    failure = PreparationResult(
        status="failed",
        reason=reason,
        request_id=request_id,
        user_message=TECHNICAL_FAILURE_MESSAGE,
    )
    runtime.stream_writer({
        "type": "error_response",
        "agent": "anomaly",
        "content": TECHNICAL_FAILURE_MESSAGE,
        "error": TECHNICAL_FAILURE_MESSAGE,
    })
    return {"agent_result": {"preparation_result": failure}}


async def run_anomaly_agent(
    state: OrchestratorState,
    runtime: Runtime[DataAgentContext],
) -> dict[str, object]:
    """把 Orchestrator 的 query 交给异动主 Graph，并返回其最终状态。

    runtime.stream_writer 写出的事件会进入上层 SSE；最终返回值则进入
    OrchestratorState.agent_result，供非流式调用或后续编排使用。当前
    主 Graph 只运行到 Skill 选择，变化检验与归因节点尚未接入。
    """
    # 先告诉前端已经进入异动分支；这是进度提示，不代表解析成功。
    runtime.stream_writer({
        "type": "progress",
        "stage": "anomaly",
        "agent": "anomaly",
        "content": "已分流到异动分析 Agent，正在提取指标并匹配分析 Skill",
    })
    # 两个 Agent 的 State 不共享：这里只把 query 映射为子 Graph 的 raw_question。
    # 意图识别与路由已在 Orchestrator 完成，不再作为输入传给 Main_RootCause_Agent。
    try:
        result = await main_root_cause_agent.ainvoke(
            {"raw_question": state.get("rewritten_query") or state.get("query", "")},
            context=runtime.context,
        )
    except Exception as exc:
        logger.exception("异动分析子图执行失败: {}", exc)
        return _technical_failure(runtime, "anomaly_execution_error")

    if not isinstance(result, dict):
        logger.error("异动分析子图返回了非对象结果")
        return _technical_failure(runtime, "invalid_anomaly_result")

    status = result.get("skill_match_status")
    if not isinstance(status, str):
        logger.error("异动分析结果缺少 skill_match_status")
        return _technical_failure(runtime, "missing_skill_match_status")

    selected_skill_name = result.get("selected_skill_name")
    runtime.stream_writer({
        "type": "skill_match_result",
        "agent": "anomaly",
        "status": status,
        "reason": result.get("skill_match_reason", ""),
        "extracted_metrics": result.get("extracted_metrics", []),
    })
    if status == "matched" and isinstance(selected_skill_name, str) and selected_skill_name.strip():
        runtime.stream_writer({
            "type": "selected_skill",
            "agent": "anomaly",
            "skill_name": selected_skill_name,
        })
    return {"agent_result": result}
