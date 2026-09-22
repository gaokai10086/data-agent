from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.logging import logger
from app.repositories.mysql.dw_mysql_repository import DWMySQLRepository
from app.service.sql_validation_service import SQLValidationService


async def validate_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    #  流式通知主动推送校验阶段事件，前端界面的步骤条此时会从“生成 SQL”切换到“验证 SQL”。
    writer({"stage": "验证SQL语句"})

    sql = state["sql"]
    #  从 runtime.context 中提取在 ChatService 打包好的 dw_mysql_repository 实例，而不是硬编码创建数据库连接
    dw_mysql_repository: DWMySQLRepository = runtime.context['dw_mysql_repository']

    try:
        #   验证表名、字段名是否存在以及语法是否合规
        await dw_mysql_repository.validate_sql(sql)

        reference_violations = await SQLValidationService(
            dw_repository=dw_mysql_repository
        ).validate_references(
            sql=sql,
            table_infos=state.get("table_infos", []),
            table_relations=state.get("table_relations", []),
            retrieved_values=state.get("retrieved_values", []),
            query=state.get("query", ""),
        )
        if reference_violations:
            current_retry = state.get("retry_count", 0) + 1
            message = "SQL引用校验失败：" + "；".join(reference_violations)
            logger.error(message)
            return {"error": message, "retry_count": current_retry}

        logger.info("SQL校验通过")
        return {"error": None}
    except Exception as e:
        logger.error(f"SQL校验失败: {e}")

        #  获取当前重试次数并+1，并将报错代码返回到stats里面
        current_retry = state.get("retry_count", 0) + 1
        return {"error": str(e), "retry_count": current_retry}

"""
dw_mysql_repository.validate_sql(sql) 底层是怎么实现的？为什么不直接执行 SQL 拿结果看报不报错？
MySQL 只做词法解析、字段/表名存在性检查、权限校验与执行计划生成，不会真正触发物理数据扫描

为什么这里把 str(e) 原封不动存进 State，而不是直接记录一个布尔值 is_valid = False？
如果只给布尔值，模型不知道错在什么地方，重新生成时极大概率重复同样的错误，如果将异常放入呢，大模型会结合异常信息，从而以极高成功率完成修复
"""
