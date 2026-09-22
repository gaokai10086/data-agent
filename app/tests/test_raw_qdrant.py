"""
================================================================================
模块名称：Qdrant 向量检索直连测试 (新版 SDK query_points 适配版)
文件路径：app/tests/test_raw_qdrant.py
================================================================================
"""
import asyncio
from app.clients.embedding_client import embedding_client_manager
from app.clients.qdrant_client import qdrant_client_manager


async def raw_search():
    print("=" * 60)
    print("🚀 正在通过新版 query_points API 直连 Qdrant 检索...")
    print("=" * 60)

    embedding_client_manager.init()
    qdrant_client_manager.init()

    emb = embedding_client_manager.client
    client = qdrant_client_manager.client

    # 1. 生成待测词汇向量
    test_word = "品牌"
    vector = emb.embed_query(test_word)

    # 2. 调用新版 Qdrant 核心查询接口 query_points
    response = await client.query_points(
        collection_name="data_agent_column",
        query=vector,
        limit=5,
        with_payload=True
    )

    # 提取命中的点位列表
    points = response.points
    print(f"\n📦 针对检索词【{test_word}】的命中数量: {len(points)}")

    for idx, p in enumerate(points):
        print(f"\n[{idx + 1}] ID: {p.id} | 得分: {p.score:.4f}")
        print(f"    Payload: {p.payload}")


if __name__ == "__main__":
    asyncio.run(raw_search())