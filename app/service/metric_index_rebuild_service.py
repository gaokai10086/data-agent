"""从 Meta 已发布指标重建 Qdrant 指标语义索引。"""
from dataclasses import dataclass
from typing import Any

from qdrant_client.http import models as rest_models

from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository


@dataclass
class MetricIndexRebuildReport:
    metrics_indexed: int = 0


class MetricIndexRebuildService:
    def __init__(
        self,
        meta_repository: MetaMySQLRepository,
        embedding_client: Any,
        qdrant_client: Any,
        collection_name: str = "data_agent_metric",
        vector_size: int = 1024,
    ):
        self.meta_repository = meta_repository
        self.embedding_client = embedding_client
        self.qdrant_client = qdrant_client
        self.collection_name = collection_name
        self.vector_size = vector_size

    async def rebuild(self) -> MetricIndexRebuildReport:
        metrics = await self.meta_repository.list_metrics_by_status("published")
        if not metrics:
            return MetricIndexRebuildReport()

        texts: list[str] = []
        payloads: list[dict[str, Any]] = []
        for metric in metrics:
            alias_text = "、".join(str(item) for item in (metric.alias or [])) or "无"
            semantic_text = (
                f"指标名称: {metric.name}, "
                f"指标定义: {metric.description or ''}, "
                f"业务别名: [{alias_text}], "
                f"计算公式: {metric.formula or ''}, "
                f"事实表: {metric.fact_table or ''}, "
                f"度量字段: {metric.measure_column or ''}"
            )
            texts.append(semantic_text)
            payloads.append(
                {
                    "id": metric.id,
                    "name": metric.name,
                    "description": metric.description or "",
                    "relevant_columns": list(metric.relevant_columns or []),
                    "alias": list(metric.alias or []),
                    "metric_key": metric.metric_key or "",
                    "formula": metric.formula or "",
                    "fact_table": metric.fact_table or "",
                    "measure_column": metric.measure_column or "",
                    "aggregation": metric.aggregation or "",
                    "time_column": metric.time_column or "",
                    "filters": list(metric.filters or []),
                    "dimensions": list(metric.dimensions or []),
                }
            )

        vectors = await self.embedding_client.aembed_documents(texts)
        if len(vectors) != len(payloads):
            raise RuntimeError("指标向量数量与指标数量不一致")

        if await self.qdrant_client.collection_exists(self.collection_name):
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
        return MetricIndexRebuildReport(metrics_indexed=len(points))
