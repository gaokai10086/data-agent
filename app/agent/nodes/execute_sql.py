from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.logging import logger
from app.repositories.mysql.dw_mysql_repository import DWMySQLRepository


async def execute_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "执行SQL语句"})

    #  #  从全局 state 中拿出最终确认合格的 SQL,从 runtime.context 中取出数据仓库访问对象 dw_mysql_repository
    sql = state["sql"]
    dw_mysql_repository: DWMySQLRepository = runtime.context['dw_mysql_repository']

    try:
        result = await dw_mysql_repository.execute_sql(sql)
        logger.info(f"SQL执行结果: {result}")
        writer({"result": result})
        # 将结果同步写回全局 State，供后续可能接入的分析/出图节点读取
        return {"sql_result": result}
    except Exception as e:
        logger.error(f"SQL执行失败: {e}")
        raise