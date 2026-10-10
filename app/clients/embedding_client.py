"""
================================================================================
模块名称：向量化嵌入客户端管理器 (智谱 embedding-3 在线版) 🌐
文件路径：app/clients/embedding_client.py
核心改进：
    1. 彻底移除本地 HuggingFace 大模型权重加载，消除冷启等待与内存占用
    2. 采用智谱最新 embedding-3 模型，显式指定 dimensions=1024 兼容 Qdrant
    3. 兼顾同步与异步调用管道，保持与现有仓储和上层 Service 完全解耦
================================================================================
"""
import os
from langchain_community.embeddings import ZhipuAIEmbeddings
from app.config.app_config import app_config



class EmbeddingClientManager:
    def __init__(self):
        self.config = getattr(app_config, "embedding", None)

        self.client: ZhipuAIEmbeddings

    def init(self):
        """初始化智谱在线嵌入模型"""
        # 优先从环境变量读取，若无则从你的配置文件中提取或硬编码备用
        api_key = app_config.embedding.zhipu_api_key
        if not api_key and self.config and hasattr(self.config, "api_key"):
            api_key = self.config.api_key


        model_name = app_config.embedding.model_name

        # 🌟 重点：dimensions=1024 利用 MRL 特性截断，无缝适配既有 Qdrant 集合
        self.client = ZhipuAIEmbeddings(
            model=model_name,
            api_key=api_key,
            dimensions=1024
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        """同步生成文本批处理向量（调用标准 embed_documents）"""
        if self.client is None:
            raise RuntimeError("Embedding 客户端尚未初始化，请先调用 init()")
        return self.client.embed_documents(texts)

    async def aembed(self, texts: list[str]) -> list[list[float]]:
        """异步生成文本批处理向量（调用标准 aembed_documents，防事件循环阻塞）"""
        if self.client is None:
            raise RuntimeError("Embedding 客户端尚未初始化，请先调用 init()")
        return await self.client.aembed_documents(texts)


# 单例实例供全局注入使用
embedding_client_manager = EmbeddingClientManager()

if __name__ == '__main__':
    import asyncio

    print("🚀 开始执行 Embedding 客户端自检测试...\n")

    # 1. 初始化客户端
    manager = EmbeddingClientManager()
    manager.init()

    # 2. 测试单文本同步向量化 (模拟用户在线提问)
    test_query = "统计每个商品品类下，销售总额排名前3名的门店"
    print(f"\n🔍 [测试1] 正在对提问生成查询向量: '{test_query}'")
    query_vector = manager.client.embed_query(test_query)

    print(f"👉 向量维度: {len(query_vector)} (必须严格等于 1024)")
    print(f"👉 向量前5位切片: {query_vector[:5]}")
    assert len(query_vector) == 1024, "❌ 向量维度不匹配，必须为 1024！"
    print("✅ 单文本查询向量测试通过！")


    # 3. 测试异步批处理向量化 (模拟元数据批量构建或金标批量导入)
    async def test_async_batch():
        test_docs = [
            "订单交易主事实表 fact_order",
            "商品维度表 dim_product"
        ]
        print(f"\n⚡ [测试2] 正在执行异步批处理嵌入: {test_docs}")
        batch_vectors = await manager.aembed(test_docs)
        print(f"👉 成功生成批处理条数: {len(batch_vectors)}")
        print(f"👉 首条批处理向量维度: {len(batch_vectors[0])}")
        assert len(batch_vectors) == 2 and len(batch_vectors[0]) == 1024
        print("✅ 异步批处理向量测试通过！")


    asyncio.run(test_async_batch())
    print("\n🎉 智谱 embedding-3 客户端全链路自检圆满成功！")
