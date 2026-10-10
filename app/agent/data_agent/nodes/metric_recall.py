"""
================================================================================
模块名称：指标向量与业务语义多路召回节点 (自愈防御版) 📈
文件路径：app/data_agent/nodes/metric_recall.py
================================================================================
"""
from typing import Any, List, Dict
from app.clients.embedding_client import embedding_client_manager
from app.clients.qdrant_client import qdrant_client_manager
# 如果你也有 MetricQdrantRepository，按实际导入；若直接用基类或类似逻辑，可按需调整
from app.core.logging import logger
from app.agent.data_agent.progress import emit_node_result

_DEFAULT_METRIC_REPO = None
_DEFAULT_EMBEDDING = None


def get_metric_components():
    """自愈初始化指标仓储与 embedding 客户端"""
    global _DEFAULT_METRIC_REPO, _DEFAULT_EMBEDDING
    if _DEFAULT_EMBEDDING is None:
        if not getattr(embedding_client_manager, "client", None):
            embedding_client_manager.init()
        _DEFAULT_EMBEDDING = embedding_client_manager.client

    if not getattr(qdrant_client_manager, "client", None):
        qdrant_client_manager.init()

    return _DEFAULT_EMBEDDING


async def metric_recall(state: Dict[str, Any], runtime: Any = None) -> Dict[str, Any]:
    """指标召回节点主逻辑"""
    logger.info("📈 [MetricRecall] 开始执行核心业务指标语义向量召回...")

    # ==================== 1. 防御性获取依赖 ====================
    embedding_client = None
    metric_repo = None

    if runtime is not None:
        context = getattr(runtime, "context", None)
        if isinstance(context, dict):
            embedding_client = context.get("embedding_client")
            metric_repo = context.get("metric_qdrant_repository")

    # 如果外部未注入，启用自愈获取
    if embedding_client is None:
        embedding_client = get_metric_components()

    query = state.get("query", "")
    keywords: List[str] = state.get("keywords", [])
    search_terms = list(dict.fromkeys([query] + keywords)) if keywords else [query]

    recalled_metrics: List[Dict[str, Any]] = []

    # 如果 metric_repo 存在，执行召回；若不存在或暂未启用，优雅降级返回空列表，绝不崩掉图流程
    if metric_repo is not None:
        for term in search_terms:
            if not term or not term.strip():
                continue
            try:
                query_vector = embedding_client.embed_query(term)
                matched = await metric_repo.search(query_vector, limit=3)
                for item in matched:
                    # 结果归一化处理
                    recalled_metrics.append(item if isinstance(item, dict) else dict(item))
            except Exception as e:
                logger.warning(f"⚠️ [MetricRecall] 指标检索词 [{term}] 波动: {e}")
    else:
        logger.info("ℹ️ [MetricRecall] 未检测到 metric_qdrant_repository 仓储，指标召回步骤优雅跳过。")

    logger.info(f"✨ [MetricRecall] 指标召回完毕，共锁定 {len(recalled_metrics)} 个核心指标。")
    emit_node_result(runtime, f"业务指标：召回 {len(recalled_metrics)} 个")

    return {
        "retrieved_metrics": recalled_metrics
    }
