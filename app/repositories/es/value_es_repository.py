import asyncio

from elasticsearch import AsyncElasticsearch

from app.clients.es_client import es_client_manager
from app.models.es.value_info_es import ValueInfoES
from app.core.meta.value_grounding import match_value_expression


class ValueESRepository:
    es_index_name = "data_agent"
    es_index_mappings = {
        "properties": {
            "id": {"type": "keyword"},
            "value": {"type": "text", "analyzer": "standard", "search_analyzer": "standard",
                      "fields": {"exact": {"type": "keyword"}}},
            "aliases": {"type": "keyword"},
            "type": {"type": "keyword"},
            "column_id": {"type": "keyword"},
            "column_name": {"type": "keyword"},
            "table_id": {"type": "keyword"},
            "table_name": {"type": "keyword"},
        }
    }

    def __init__(self, es_client: AsyncElasticsearch):
        self.es_client = es_client

    async def ensure_index(self):
        if not await self.es_client.indices.exists(index=self.es_index_name):
            await self.es_client.indices.create(index=self.es_index_name,
                                                mappings=self.es_index_mappings)
        else:
            # 增加多字段不会删除现有索引。旧文档仍可通过候选检索及完整值校验召回。
            await self.es_client.indices.put_mapping(
                index=self.es_index_name,
                properties={"value": self.es_index_mappings["properties"]["value"],
                            "aliases": {"type": "keyword"}},
            )

    async def batch_index(self, docs: list[ValueInfoES], batch_size: int = 10):

        for i in range(0, len(docs), batch_size):
            batch = docs[i:i + batch_size]
            operations = []

            for doc in batch:
                operations.append({
                    "index": {
                        "_index": self.es_index_name,
                        "_id": doc["id"]
                    }
                })
                operations.append(doc)

            response = await self.es_client.bulk(operations=operations)
            if response.get("errors"):
                raise RuntimeError("枚举值索引批量写入失败，请检查 ES 返回的写入错误")

    async def query(self, query: str, score_threshold: float = 0.6, limit: int = 10) -> list[ValueInfoES]:

        query = query.strip()
        if not query:
            return []
        es_query = {"bool": {"should": [
            {"term": {"value.exact": {"value": query, "boost": 10}}},
            {"term": {"aliases": {"value": query, "boost": 10}}},
            {"match": {"value": query}},
        ], "minimum_should_match": 1}}

        resp = await self.es_client.search(
            index=self.es_index_name,
            query=es_query,
            # 分数用于候选排序；完整值/别名校验才决定是否为有效证据。
            size=max(50, limit * 5)
        )

        hits = resp.get("hits", {}).get("hits", [])

        results: list[ValueInfoES] = []
        for hit in hits:
            source = hit["_source"]
            expression = match_value_expression(query, source)
            if expression is not None:
                results.append({**source, "matched_text": expression})
                if len(results) >= limit:
                    break

        return results


if __name__ == '__main__':
    async def test():
        es_client_manager.init()
        es_client = es_client_manager.client
        full_text_repository = ValueESRepository(es_client)
        query = "统计一下手机产品的销量"
        print(await full_text_repository.query(query=query))
        await es_client_manager.close()


    asyncio.run(test())
