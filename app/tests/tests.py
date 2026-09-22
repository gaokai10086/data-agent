from qdrant_client import AsyncQdrantClient
import asyncio

async def check():
    client = AsyncQdrantClient(host="localhost", port=6333)
    count = await client.count(collection_name="data_agent_column", exact=True)
    print(f"Qdrant data_agent_column 里的向量总数: {count}")

asyncio.run(check())