"""
================================================================================
模块名称：Qdrant 向量检索直连孤立测试脚本 (根目录安全运行版)
文件路径：test_qdrant_root.py
================================================================================
"""
import asyncio
from app.clients.embedding_client import embedding_client_manager
from app.clients.qdrant_client import qdrant_client_manager
from app.repositories.qdrant.column_repository_qdrant import ColumnQdrantRepository


async def test_direct_search():
    print("=" * 60)
    print("🚀 开始直连 Qdrant 向量数据库测试...")
    print("=" * 60)

    # 1. 初始化客户端
    if not getattr(embedding_client_manager, "client", None):
        embedding_client_manager.init()
    embedding_client = embedding_client_manager.client

    if not getattr(qdrant_client_manager, "client", None):
        qdrant_client_manager.init()
    qdrant_client = qdrant_client_manager.client

    # 2. 实例化仓储
    repo = ColumnQdrantRepository(qdrant_client)

    # 3. 测试几个核心业务词汇
    test_terms = ["品牌", "金额", "销售", "销售总额", "商品"]

    for term in test_terms:
        print(f"\n----------------------------------------")
        print(f"🔎 测试检索词: 【{term}】")

        # 文本转向量
        vector = embedding_client.embed_query(term)

        # 仓库搜索
        matched_items = await repo.search(vector, limit=3)
        print(f"📦 命中结果数: {len(matched_items)}")

        for idx, item in enumerate(matched_items):
            payload = {}
            score = 0.0
            if hasattr(item, "payload"):
                payload = item.payload or {}
                score = getattr(item, "score", 0.0)
            elif isinstance(item, dict):
                payload = item.get("payload", item) or {}
                score = item.get("score", 0.0)

            table_name = payload.get("table_name") or payload.get("table")
            column_name = payload.get("column_name") or payload.get("column")
            print(f"   [{idx + 1}] 表名: {table_name} | 字段名: {column_name} | 得分: {score:.4f}")


if __name__ == "__main__":
    asyncio.run(test_direct_search())