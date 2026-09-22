"""
================================================================================
脚本名称：Few-Shot 金标 SQL 向量库清理重置工具 (异步版) 🧹🗑️
文件路径：app/scripts/clean_sql_examples_qdrant.py
核心职责：
    1. 采用 AsyncIO 规范安全连接异步 QdrantClient
    2. 彻底删除 data_agent_sql_examples 集合及历史测试点位
    3. 重新以标准参数（1024维、余弦距离）初始化干净的空集合
================================================================================
"""
import sys
import asyncio
from pathlib import Path
from qdrant_client.http import models

# 将项目根目录挂载到系统路径
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(ROOT_DIR))

from app.clients.qdrant_client import qdrant_client_manager
from app.core.logging import logger

COLLECTION_NAME = "data_agent_sql_examples"
VECTOR_DIMENSION = 1024  # BAAI/bge-large-zh-v1.5 稠密向量维度


async def reset_gold_sql_collection():
    """执行金标向量集合的异步删除与重建"""
    logger.info(f"🚀 开始重置 Qdrant 金标集合: [{COLLECTION_NAME}]...")

    # 1. 安全初始化客户端
    if not getattr(qdrant_client_manager, "client", None):
        qdrant_client_manager.init()
    client = qdrant_client_manager.client

    try:
        # 2. 异步检查集合是否存在
        collections_response = await client.get_collections()
        existing_collections = [c.name for c in collections_response.collections]

        if COLLECTION_NAME in existing_collections:
            # 获取当前集合内的历史数据统计
            collection_info = await client.get_collection(COLLECTION_NAME)
            points_count = getattr(collection_info, "points_count", 0)
            logger.warning(f"⚠️ 检测到历史集合 [{COLLECTION_NAME}] 存在，历史点位数量: {points_count}")

            # 异步删除集合
            await client.delete_collection(collection_name=COLLECTION_NAME)
            logger.info(f"🗑️ 已成功彻底删除历史测试集合: [{COLLECTION_NAME}]")
        else:
            logger.info(f"ℹ️ 集合 [{COLLECTION_NAME}] 原本不存在，无需删除。")

        # 3. 异步重新创建全新的空白集合
        await client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=models.VectorParams(
                size=VECTOR_DIMENSION,
                distance=models.Distance.COSINE
            )
        )
        logger.info(f"✨ 全新干净的集合 [{COLLECTION_NAME}] 初始化就绪！维度: {VECTOR_DIMENSION}, 度量: COSINE")

    except Exception as e:
        logger.error(f"❌ 重置集合异常: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(reset_gold_sql_collection())