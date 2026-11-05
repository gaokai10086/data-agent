"""DataQueryTool 真实服务集成测试。

该测试会真实调用 MySQL、Qdrant、Elasticsearch、Embedding 服务、LLM
以及现有 data_query_graph。它不使用 Mock，也不修改任何业务数据。
"""

from __future__ import annotations

import os
import unittest

from langgraph.runtime import Runtime

from app.agent.anomaly_agent.common.data_query import DataQueryRequest, DataQueryTool
from app.agent.data_agent.context import DataAgentContext
from app.clients.embedding_client import embedding_client_manager
from app.clients.es_client import es_client_manager
from app.clients.mysql_client import dw_client_manager, meta_client_manager
from app.clients.qdrant_client import qdrant_client_manager
from app.config.app_config import DEFAULT_LLM_MODEL, DEFAULT_LLM_PROVIDER
from app.repositories.es.value_es_repository import ValueESRepository
from app.repositories.mysql.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository
from app.repositories.qdrant.column_repository_qdrant import ColumnQdrantRepository
from app.repositories.qdrant.metric_repository_qdrant import MetricQdrantRepository


class DataQueryToolIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """验证公共工具可以驱动真实的智能问数取数链路。"""

    async def test_real_data_query_tool(self):
        query = os.getenv("ANOMALY_DATA_QUERY", "查询2025年7月的销售额")
        events: list[dict] = []

        meta_client_manager.init()
        dw_client_manager.init()
        qdrant_client_manager.init()
        es_client_manager.init()
        embedding_client_manager.init()

        try:
            async with (
                meta_client_manager.session_factory() as meta_session,
                dw_client_manager.session_factory() as dw_session,
            ):
                context: DataAgentContext = {
                    "meta_mysql_repository": MetaMySQLRepository(meta_session),
                    "dw_mysql_repository": DWMySQLRepository(dw_session),
                    "dw_session_factory": dw_client_manager.session_factory,
                    "metric_qdrant_repository": MetricQdrantRepository(
                        qdrant_client_manager.client
                    ),
                    "column_qdrant_repository": ColumnQdrantRepository(
                        qdrant_client_manager.client
                    ),
                    "value_es_repository": ValueESRepository(es_client_manager.client),
                    "embedding_client": embedding_client_manager.client,
                    "llm_provider": DEFAULT_LLM_PROVIDER,
                    "llm_model_tier": DEFAULT_LLM_MODEL,
                }
                runtime = Runtime(context=context, stream_writer=events.append)
                request = DataQueryRequest(
                    query=query,
                    request_id="integration-data-query",
                    purpose="anomaly_integration",
                    expected_columns=[],
                    max_rows=100,
                )

                result = await DataQueryTool().run(request, runtime)

        finally:
            await dw_client_manager.close()
            await meta_client_manager.close()
            await qdrant_client_manager.close()
            await es_client_manager.close()

        self.assertTrue(
            result.success,
            f"真实数据查询失败：{result.error_code} / {result.error_message}",
        )
        self.assertTrue(result.sql, "真实查询必须返回 SQL 证据")
        self.assertIsInstance(result.rows, list)
        self.assertLessEqual(len(result.rows), 100)
        self.assertTrue(events, "真实查询应产生至少一个过程事件")


if __name__ == "__main__":
    unittest.main()
