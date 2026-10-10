"""
================================================================================
模块名称：字段向量与业务语义多路召回节点 🔍
文件路径：app/data_agent/nodes/column_recall.py
================================================================================
"""
from typing import Any, List, Dict
from app.clients.embedding_client import embedding_client_manager
from app.clients.qdrant_client import qdrant_client_manager
from app.repositories.qdrant.column_repository_qdrant import ColumnQdrantRepository
from app.core.logging import logger
from app.agent.data_agent.progress import emit_node_result

_DEFAULT_COLUMN_REPO = None
_EMBEDDING_CLIENT = None


def get_default_components():
    global _DEFAULT_COLUMN_REPO, _EMBEDDING_CLIENT
    if _DEFAULT_COLUMN_REPO is None:
        if not getattr(embedding_client_manager, "client", None):
            embedding_client_manager.init()
        _EMBEDDING_CLIENT = embedding_client_manager.client

        if not getattr(qdrant_client_manager, "client", None):
            qdrant_client_manager.init()
        _DEFAULT_COLUMN_REPO = ColumnQdrantRepository(qdrant_client_manager.client)
    return _DEFAULT_COLUMN_REPO, _EMBEDDING_CLIENT


async def column_recall(state: Dict[str, Any], runtime: Any = None) -> Dict[str, Any]:
    logger.info("🔍 [ColumnRecall] 开始执行物理字段与语义向量召回...")
    repo, embedding_client = get_default_components()

    keywords: List[str] = state.get("keywords", [])
    query = state.get("query", "")

    # 优先使用短关键词检索，提升命中精度
    valid_keywords = [kw for kw in keywords if len(kw) < 15]
    search_terms = list(dict.fromkeys(valid_keywords + [query])) if valid_keywords else [query]

    recalled_columns: List[Dict[str, Any]] = []
    seen_signatures = set()

    for term in search_terms:
        if not term or not term.strip():
            continue
        try:
            query_vector = embedding_client.embed_query(term)
            # 调用仓储检索
            matched_items = await repo.search(query_vector, limit=10)

            for item in matched_items:
                # 兼容处理：获取 payload 字典与分数
                payload = item if isinstance(item, dict) else getattr(item, "payload", {})
                score = payload.get("score", 0.0) if isinstance(item, dict) else getattr(item, "score", 0.0)

                # 精确提取你 Qdrant 里的真实键名
                table_name = payload.get("table_name")
                column_name = payload.get("column_name")
                comment = payload.get("comment", "")
                col_id = payload.get("column_id", "")
                table_id = payload.get("table_id", "")
                data_type = payload.get("data_type", "VARCHAR")
                alias = payload.get("alias", [])
                role = payload.get("role", "dimension")  # 安全获取 role 字段，若无则默认为 dimension

                if table_name and column_name:
                    sig = f"{table_name}.{column_name}"
                    if sig not in seen_signatures:
                        seen_signatures.add(sig)
                        # 严格按照下游 merge_retrieved_info 需要的契约组装
                        recalled_columns.append({
                            "id": col_id,
                            "table_id": table_id,
                            "table_name": table_name,
                            "name": column_name,
                            "column_name": column_name,
                            "type": data_type,
                            "role": role,                # <--- 新增此行，彻底解决 KeyError: 'role'
                            "description": comment,
                            "comment": comment,
                            "alias": alias,
                            "examples": [],
                            "score": score
                        })
        except Exception as e:
            logger.warning(f"⚠️ [ColumnRecall] 词项 [{term}] 检索波动: {e}")

    logger.info(f"✨ [ColumnRecall] 召回成功，共锁定 {len(recalled_columns)} 个候选列")
    emit_node_result(runtime, f"候选字段：召回 {len(recalled_columns)} 个")
    return {"retrieved_columns": recalled_columns}
