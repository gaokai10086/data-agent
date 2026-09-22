from qdrant_client.http import models as rest_models
from app.repositories.qdrant.base_repository_qdrant import BaseQdrantRepository
from app.models.qdrant.sql_example_info_qdrant import SQLExampleQdrant

class SQLExampleQdrantRepository(BaseQdrantRepository[SQLExampleQdrant]):
    """基于 BaseQdrantRepository 泛型基类的金标 SQL 向量仓储"""
    collection_name = "data_agent_sql_examples"

    async def save_examples_with_vectors(self, examples_with_vectors: list):
        """批量保存金标样本及其向量到 Qdrant"""
        points = []
        for example_payload, vector in examples_with_vectors:
            points.append(
                rest_models.PointStruct(
                    id=example_payload["id"],
                    vector=vector,
                    payload=example_payload
                )
            )

        if points:
            # 直接调用父类继承的 qdrant 客户端执行 upsert
            await self.client.upsert(
                collection_name=self.collection_name,
                points=points
            )

    async def list_examples(self, limit: int = 200) -> list[dict]:
        """读取当前 Qdrant 集合中的全部金标 SQL 样例，供管理页面展示。"""
        records, _ = await self.client.scroll(
            collection_name=self.collection_name,
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        return [dict(record.payload) for record in records if record.payload]

    async def delete_example(self, example_id: str) -> None:
        """按向量点 ID 删除一条金标 SQL 样例。"""
        await self.client.delete(
            collection_name=self.collection_name,
            points_selector=[example_id],
        )
