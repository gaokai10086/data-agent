"""
================================================================================
模块名称：枚举值与关键词 Elasticsearch 召回节点 (自愈防御版)🔎
文件路径：app/agent/nodes/value_recall.py
================================================================================
"""
from typing import Any, List, Dict
from elasticsearch import AsyncElasticsearch
from app.config.app_config import app_config
from app.core.logging import logger

_DEFAULT_ES_CLIENT = None


def get_default_es_client():
    """自愈初始化默认 Elasticsearch 客户端"""
    global _DEFAULT_ES_CLIENT
    if _DEFAULT_ES_CLIENT is None:
        logger.info("🔄 [ValueRecall] 正在按配置初始化默认 Elasticsearch 客户端...")
        _DEFAULT_ES_CLIENT = AsyncElasticsearch(
            hosts=[f"http://{app_config.elasticsearch.host}:{app_config.elasticsearch.port}"]
        )
    return _DEFAULT_ES_CLIENT


async def value_recall(state: Dict[str, Any], runtime: Any = None) -> Dict[str, Any]:
    """枚举值向量/倒排召回节点"""
    logger.info("🔎 [ValueRecall] 开始执行 Elasticsearch 枚举值与倒排索引召回...")

    # ==================== 1. 防御性获取依赖 ====================
    value_es_repository = None

    if runtime is not None:
        context = getattr(runtime, "context", None)
        if isinstance(context, dict):
            value_es_repository = context.get("value_es_repository")

    keywords: List[str] = state.get("keywords", [])
    recalled_values: List[Dict[str, Any]] = []

    # ==================== 2. 执行召回或优雅跳过 ====================
    if value_es_repository is not None:
        for kw in keywords:
            if not kw or not kw.strip():
                continue
            try:
                # ========== 关键修复：使用 query 方法而非 search ==========
                matched = await value_es_repository.query(kw, limit=5)
                for item in matched:
                    recalled_values.append(item if isinstance(item, dict) else dict(item))
            except Exception as e:
                logger.warning(f"⚠️ [ValueRecall] 关键词 [{kw}] 检索波动: {e}")
    else:
        logger.info("ℹ️ [ValueRecall] 未检测到 value_es_repository 仓储，枚举值召回步骤优雅跳过。")

    logger.info(f"✨ [ValueRecall] 枚举值召回完毕，共锁定 {len(recalled_values)} 个匹配项。")

    return {
        "retrieved_values": recalled_values
    }
