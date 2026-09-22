"""单题执行器：调用 Agent 并把结果整理成可比较的结构。"""
import time
from dataclasses import dataclass, field

from app.agent.graph import graph
from app.clients.embedding_client import embedding_client_manager
from app.clients.es_client import es_client_manager
from app.clients.qdrant_client import qdrant_client_manager
from app.evaluation.comparator import jsonify_rows, results_match
from app.evaluation.schema import EvalCase
from app.repositories.es.value_es_repository import ValueESRepository
from app.repositories.mysql.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository
from app.repositories.qdrant.column_repository_qdrant import ColumnQdrantRepository
from app.repositories.qdrant.metric_repository_qdrant import MetricQdrantRepository
from app.service.chat_service import ChatService


@dataclass
class CaseResult:
    case_id: str
    question: str
    category: str
    passed: bool
    latency_ms: float
    actual_sql: str = ""
    actual_result: list = field(default_factory=list)
    expected_result: list = field(default_factory=list)
    error: str = ""
    failure_reason: str = ""
    schema_linking_recall: float = 0.0
    join_accuracy: float = 0.0


def build_chat_service(meta_session, dw_session) -> ChatService:
    """按当前请求构建 ChatService，保证评测与线上使用同一条链路。"""
    return ChatService(
        graph=graph,
        embedding_client=embedding_client_manager.client,
        meta_mysql_repository=MetaMySQLRepository(meta_session),
        dw_mysql_repository=DWMySQLRepository(dw_session),
        column_qdrant_repository=ColumnQdrantRepository(qdrant_client_manager.client),
        value_es_repository=ValueESRepository(es_client_manager.client),
        metric_qdrant_repository=MetricQdrantRepository(qdrant_client_manager.client),
    )


async def refresh_gold(cases: list[EvalCase], dw_repository: DWMySQLRepository) -> list[EvalCase]:
    """执行 gold_sql，把真实结果写成标准答案快照。"""
    for case in cases:
        if not case.gold_sql:
            continue
        rows = await dw_repository.execute_sql(case.gold_sql)
        case.expected_result = jsonify_rows(rows)
    return cases


async def run_case(chat_service: ChatService, case: EvalCase, numeric_scale: int = 2) -> CaseResult:
    started = time.perf_counter()
    try:
        final_state = await chat_service.run_once(case.question)
        error = ""
    except Exception as exc:  # 单题失败不能中断整批评测
        return CaseResult(
            case_id=case.id,
            question=case.question,
            category=case.category,
            passed=False,
            latency_ms=(time.perf_counter() - started) * 1000,
            error=str(exc),
            failure_reason="agent_exception",
        )

    latency_ms = (time.perf_counter() - started) * 1000
    actual_sql = final_state.get("sql") or ""
    actual_result = final_state.get("sql_result") or []
    error = final_state.get("error") or ""

    actual_tables = {
        str(table.get("name"))
        for table in final_state.get("table_infos", [])
        if table.get("name")
    }
    expected_tables = set(case.tables)
    schema_linking_recall = (
        len(expected_tables & actual_tables) / len(expected_tables) if expected_tables else 1.0
    )
    if len(expected_tables) <= 1:
        join_accuracy = 1.0
    else:
        join_accuracy = 1.0 if (
            expected_tables <= actual_tables and final_state.get("table_relations")
        ) else 0.0

    if case.expect_no_sql:
        passed = not actual_result
        failure_reason = "" if passed else "expect_no_sql_but_returned_data"
    elif error:
        passed = False
        failure_reason = f"agent_error: {error}"
    else:
        passed = results_match(actual_result, case.expected_result, numeric_scale)
        failure_reason = "" if passed else "result_mismatch"

    return CaseResult(
        case_id=case.id,
        question=case.question,
        category=case.category,
        passed=passed,
        latency_ms=latency_ms,
        actual_sql=actual_sql,
        actual_result=actual_result,
        expected_result=case.expected_result,
        error=error,
        failure_reason=failure_reason,
        schema_linking_recall=schema_linking_recall,
        join_accuracy=join_accuracy,
    )
