"""对现有 ``data_query_graph`` 的公共工具适配层。"""

from __future__ import annotations

from typing import Any

from langgraph.runtime import Runtime

from app.agent.anomaly_agent.common.data_query.schemas import DataQueryRequest, DataQueryResult
from app.agent.data_agent.context import DataAgentContext
from app.agent.data_agent.query_graph import data_query_graph
from app.agent.data_agent.state import DataAgentState
from app.core.context import query_task_context, query_task_ctx_var
from app.repositories.mysql.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository


class DataQueryTool:
    """供 Agent 调用的只取数工具。

    工具负责创建独立 DW Session、调用查询子图、转发查询进度并把内部
    ``DataAgentState`` 收敛成 ``DataQueryResult``。SQL 生成、校验、修正和
    执行仍由现有 ``data_query_graph`` 完成。
    """

    async def run(
        self,
        request: DataQueryRequest,
        runtime: Runtime[DataAgentContext],
    ) -> DataQueryResult:
        session_factory = runtime.context.get("dw_session_factory")
        if session_factory is None:
            return self._failure(request, "missing_session_factory", "缺少 dw_session_factory")

        result: dict[str, Any] = {}
        try:
            # 每个工具调用使用独立 Session，允许多个 Agent 查询并发执行。
            meta_session_factory = runtime.context.get("meta_session_factory")
            if meta_session_factory is None:
                return self._failure(
                    request,
                    "missing_meta_session_factory",
                    "缺少 meta_session_factory，不能安全并发执行查询",
                )
            # Each DataQuery invocation owns both database sessions.  Reusing a
            # repository from the parent RuntimeContext would reuse its
            # AsyncSession across concurrent graph tasks, which SQLAlchemy
            # explicitly disallows.
            async with meta_session_factory() as meta_session, session_factory() as dw_session:
                query_context = dict(runtime.context)
                query_context["dw_mysql_repository"] = DWMySQLRepository(dw_session)
                query_context["meta_mysql_repository"] = MetaMySQLRepository(meta_session)
                stream = None
                with query_task_context(
                    request_id=request.request_id,
                    role=request.role or request.purpose,
                    period=request.period,
                    metric_key=request.metric_key,
                ):
                    stream = data_query_graph.astream(
                        input=DataAgentState(query=request.query),
                        context=query_context,
                        stream_mode=["custom", "values"],
                    )
                    try:
                        async for mode, chunk in stream:
                            if mode == "custom":
                                self._forward_event(runtime, chunk, request)
                            elif mode == "values" and isinstance(chunk, dict):
                                result = chunk
                    finally:
                        close_stream = getattr(stream, "aclose", None)
                        if close_stream is not None:
                            await close_stream()
        except Exception as exc:
            return self._failure(request, "query_exception", str(exc))

        error_message = result.get("error_response") or result.get("error")
        if error_message:
            return self._failure(
                request,
                result.get("sql_error_kind") or "query_failed",
                str(error_message),
                sql=result.get("sql"),
                metadata=self._safe_metadata(result),
            )

        rows = result.get("sql_result")
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            return self._failure(
                request,
                "invalid_result",
                "查询结果不是结构化行列表",
                sql=result.get("sql"),
                metadata=self._safe_metadata(result),
            )

        if request.max_rows is not None and len(rows) > request.max_rows:
            return self._failure(
                request,
                "result_limit_exceeded",
                f"查询结果超过最大行数限制：{request.max_rows}",
                sql=result.get("sql"),
                metadata=self._safe_metadata(result),
            )

        columns = list(rows[0].keys()) if rows else []
        missing_columns = [column for column in request.expected_columns if column not in columns]
        if missing_columns:
            return self._failure(
                request,
                "missing_expected_columns",
                f"查询结果缺少字段：{', '.join(missing_columns)}",
                sql=result.get("sql"),
                metadata=self._safe_metadata(result),
            )

        return DataQueryResult(
            success=True,
            rows=rows,
            sql=result.get("sql"),
            columns=columns,
            request_id=request.request_id,
            purpose=request.purpose,
            metadata={
                "role": request.role,
                "period": request.period,
                "metric_key": request.metric_key,
            },
        )

    @staticmethod
    def _forward_event(
        runtime: Runtime[DataAgentContext],
        chunk: Any,
        request: DataQueryRequest,
    ) -> None:
        scope = query_task_ctx_var.get()
        labels = {
            "request_id": request.request_id,
            "purpose": request.purpose,
            "period": request.period,
            "query_role": request.role,
            "query_task_id": scope.task_id if scope else "",
        }
        if isinstance(chunk, dict):
            runtime.stream_writer({**chunk, **labels})
        else:
            runtime.stream_writer({"event": chunk, **labels})

    @staticmethod
    def _failure(
        request: DataQueryRequest,
        error_code: str,
        error_message: str,
        *,
        sql: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DataQueryResult:
        return DataQueryResult(
            success=False,
            sql=sql,
            error_code=error_code,
            error_message=error_message,
            request_id=request.request_id,
            purpose=request.purpose,
            metadata=metadata or {},
        )

    @staticmethod
    def _safe_metadata(result: dict[str, Any]) -> dict[str, Any]:
        """只保留可供调用方诊断的元数据，避免泄漏内部 State。"""
        return {
            key: result[key]
            for key in ("sql_error_kind", "sql_error_stage", "retry_count")
            if result.get(key) is not None
        }

