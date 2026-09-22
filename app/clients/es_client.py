from elasticsearch import AsyncElasticsearch

from app.config.app_config import ESConfig, app_config


class ESClientManager:
    """Elasticsearch 客户端管理器（适配 ES 9.x）"""

    def __init__(self, config: ESConfig):
        self.config = config
        self.client: AsyncElasticsearch | None = None

    def _get_url(self):
        return f"http://{self.config.host}:{self.config.port}"

    def init(self):
        """初始化 Elasticsearch 客户端（ES 9.x 兼容）"""
        self.client = AsyncElasticsearch(
            hosts=[self._get_url()],
            # ✅ ES 9.x 需要显式指定 API 版本兼容性
            headers={
                "Accept": "application/vnd.elasticsearch+json;compatible-with=9",
                "Content-Type": "application/vnd.elasticsearch+json;compatible-with=9"
            },
            # ✅ 忽略版本不匹配警告（开发环境）
            verify_certs=False,
            # ✅ 连接超时设置
            request_timeout=30,
            # ✅ 最大重试次数
            max_retries=3,
            # ✅ 重试超时
            retry_on_timeout=True
        )

    async def close(self):
        """关闭连接"""
        if self.client:
            await self.client.close()


# 单例模式
es_client_manager = ESClientManager(app_config.es)
