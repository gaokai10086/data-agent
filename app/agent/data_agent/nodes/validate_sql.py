from app.service.sql_error_handling import SQLReferenceError, run_sql_operation, sql_failure
from langgraph.runtime import Runtime

from app.agent.data_agent.context import DataAgentContext
from app.agent.data_agent.state import DataAgentState
from app.core.logging import logger
from app.repositories.mysql.dw_mysql_repository import DWMySQLRepository
from app.service.sql_validation_service import SQLValidationService
from app.agent.data_agent.progress import emit_node_result


async def validate_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    #  流式通知主动推送校验阶段事件，前端界面的步骤条此时会从“生成 SQL”切换到“验证 SQL”。
    writer({"stage": "验证SQL语句"})

    sql = state["sql"]
    #  从 runtime.context 中提取在 ChatService 打包好的 dw_mysql_repository 实例，而不是硬编码创建数据库连接
    dw_mysql_repository: DWMySQLRepository = runtime.context['dw_mysql_repository']

    async def validate():
        validator = SQLValidationService(dw_repository=dw_mysql_repository)
        await validator.validate_read_only_sql(sql)
        violations = await validator.validate_references(
            sql=sql,
            table_infos=state.get("table_infos", []),
            table_relations=state.get("table_relations", []),
            retrieved_values=state.get("retrieved_values", []),
            query=state.get("query", ""),
        )
        if violations:
            raise SQLReferenceError("SQL引用校验失败：" + "；".join(violations))

    try:
        await run_sql_operation(dw_mysql_repository, validate)
        logger.info("SQL校验通过")
        emit_node_result(runtime, "SQL 校验通过")
        return {"error": None, "sql_error_kind": None, "sql_error_stage": None}
    except Exception as exc:
        return sql_failure(state, exc, "validate")
