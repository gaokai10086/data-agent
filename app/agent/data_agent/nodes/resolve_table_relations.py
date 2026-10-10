"""根据候选表从 Meta 解析已确认的 Join 关系。"""
from langgraph.runtime import Runtime

from app.agent.data_agent.context import DataAgentContext
from app.agent.data_agent.state import DataAgentState
from app.core.logging import logger
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository


def _get_meta_repository(runtime: Runtime[DataAgentContext]) -> MetaMySQLRepository | None:
    context = getattr(runtime, "context", None)
    if context is None:
        return None
    if isinstance(context, dict):
        return context.get("meta_mysql_repository")
    return getattr(context, "meta_mysql_repository", None)


async def resolve_table_relations(
    state: DataAgentState,
    runtime: Runtime[DataAgentContext],
) -> dict:
    writer = getattr(runtime, "stream_writer", None)
    if writer:
        writer({"stage": "解析表间关联"})

    repository = _get_meta_repository(runtime)
    if repository is None:
        logger.warning("⚠️ [ResolveRelations] 未检测到 meta_mysql_repository，跳过关系解析")
        return {"table_relations": []}

    table_names = [
        str(table.get("name"))
        for table in state.get("table_infos", [])
        if table.get("name")
    ]
    relations = await repository.list_relations_for_tables(table_names)
    logger.info(
        f"🔗 [ResolveRelations] 候选表 [{', '.join(table_names)}] "
        f"解析到 {len(relations)} 条 Join 关系"
    )
    return {"table_relations": relations}
