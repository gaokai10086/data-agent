"""从 Meta 已发布字段重建 Qdrant 字段语义索引。"""
from dataclasses import dataclass
from typing import Any

from qdrant_client.http import models as rest_models

from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository


@dataclass
class ColumnIndexRebuildReport:
    columns_indexed: int = 0


class ColumnIndexRebuildService:
    def __init__(
        self,
        meta_repository: MetaMySQLRepository,
        embedding_client: Any,
        qdrant_client: Any,
        collection_name: str = "data_agent_column",
        vector_size: int = 1024,
    ):
        self.meta_repository = meta_repository
        self.embedding_client = embedding_client
        self.qdrant_client = qdrant_client
        self.collection_name = collection_name
        self.vector_size = vector_size

    async def rebuild(self) -> ColumnIndexRebuildReport:
        table_columns = await self.meta_repository.list_columns_with_tables()
        if not table_columns:
            return ColumnIndexRebuildReport()

        texts: list[str] = []
        payloads: list[dict[str, Any]] = []
        for table, column in table_columns:
            alias_text = "、".join(str(item) for item in (column.alias or [])) or "无"
            description = column.description or column.name or ""
            semantic_text = (
                f"表名: {table.name} ({table.description or table.name}), "
                f"字段名: {column.name}, 中文业务含义: {description}, "
                f"业务常用别名同义词: [{alias_text}], 物理类型: {column.type or ''}"
            )
            texts.append(semantic_text)
            payloads.append(
                {
                    "id": column.id,
                    "column_id": column.id,
                    "table_id": table.id,
                    "table_name": table.name,
                    "column_name": column.name,
                    "name": column.name,
                    "comment": description,
                    "description": description,
                    "alias": column.alias or [],
                    "examples": column.examples or [],
                    "data_type": column.type or "",
                    "role": column.role or "dimension",
                }
            )

        vectors = await self.embedding_client.aembed_documents(texts)
        if len(vectors) != len(payloads):
            raise RuntimeError(
                f"向量数量 [{len(vectors)}] 与字段数量 [{len(payloads)}] 不一致"
            )

        exists = await self.qdrant_client.collection_exists(self.collection_name)
        if exists:
            await self.qdrant_client.delete_collection(self.collection_name)
        await self.qdrant_client.create_collection(
            collection_name=self.collection_name,
            vectors_config=rest_models.VectorParams(
                size=self.vector_size,
                distance=rest_models.Distance.COSINE,
            ),
        )

        points = [
            rest_models.PointStruct(id=payload["id"], vector=vector, payload=payload)
            for payload, vector in zip(payloads, vectors)
        ]
        await self.qdrant_client.upsert(collection_name=self.collection_name, points=points)
        return ColumnIndexRebuildReport(columns_indexed=len(points))
