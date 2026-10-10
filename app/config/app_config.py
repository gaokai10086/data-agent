"""
================================================================================
模块名称：全系统微服务集中配置解析中心 ⚙️
文件路径：app/config/app_config.py
核心功能：
    1. 基于 Pydantic V2 强契约解析环境变量
    2. 基于文件绝对路径锚定工程根目录，防 CWD 漂移
    3. 支持嵌套命名空间式点号调用 (如 app_config.embedding.zhipu_api_key)
================================================================================
"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field


# ----------------- 1. 结构化配置契约类 -----------------

class LogFileConfig(BaseModel):
    enable: bool = True
    level: str = "INFO"
    path: str = "logs"
    rotation: str = "10 MB"
    retention: str = "7 days"


class LogConsoleConfig(BaseModel):
    enable: bool = True
    level: str = "INFO"


class LoggingConfig(BaseModel):
    file: LogFileConfig = Field(default_factory=LogFileConfig)
    console: LogConsoleConfig = Field(default_factory=LogConsoleConfig)


class DatabaseConfig(BaseModel):
    host: str = "localhost"
    port: int = 3306
    user: str
    password: str
    database: str

    @property
    def url(self) -> str:
        """组装标准 SQLAlchemy 异步/同步连接串"""
        return f"mysql+pymysql://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}?charset=utf8mb4"


class QdrantConfig(BaseModel):
    host: str = "localhost"
    port: int = 6333
    embedding_size: int = 1024
    metric_collection: str = "data_agent_metric"
    column_collection: str = "data_agent_column"


class EmbeddingConfig(BaseModel):
    zhipu_api_key: str
    model_name: str = "embedding-3"


class ESConfig(BaseModel):
    host: str = "localhost"
    port: int = 9200
    index_name: str = "data_agent"


class LLMConfig(BaseModel):
    api_key: str
    base_url: str = "https://api.deepseek.com/v1"
    models: list[str] = Field(default_factory=lambda: ["deepseek-v4-flash", "deepseek-v4-pro"])


class ZhipuLLMConfig(BaseModel):
    """智谱对话模型配置。

    配置文件推荐使用模型 ID 列表；加载后会规范化为 Provider 使用的字典。
    因为展示、路由传递和 API 调用使用同一个名称，不需要额外别名转换。
    """

    api_key: str
    base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    models: dict[str, str] = Field(default_factory=lambda: {"glm-5.3": "glm-5.3"})


# ----------------- 2. 根配置容器与加载器 -----------------

class AppConfig(BaseModel):
    logging: LoggingConfig
    db_meta: DatabaseConfig
    db_dw: DatabaseConfig
    qdrant: QdrantConfig
    embedding: EmbeddingConfig
    es: ESConfig
    llm: LLMConfig
    zhipu_llm: ZhipuLLMConfig

    @classmethod
    def load_from_env(cls) -> "AppConfig":
        """从环境变量创建运行配置；敏感信息不应写入仓库。"""
        required = (
            "DB_META_USER", "DB_META_PASSWORD", "DB_META_DATABASE",
            "DB_DW_USER", "DB_DW_PASSWORD", "DB_DW_DATABASE",
            "ZHIPU_API_KEY", "DEEPSEEK_API_KEY",
        )
        missing = [name for name in required if not os.getenv(name)]
        if missing:
            raise RuntimeError(
                "缺少必要环境变量: " + ", ".join(missing) +
                "。请复制 .env 为 .env 后填写本机凭证。"
            )

        zhipu_models_raw = os.getenv("ZHIPU_MODELS", "")
        if zhipu_models_raw:
            try:
                zhipu_models = json.loads(zhipu_models_raw)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    "ZHIPU_MODELS 必须是 JSON 模型列表，例如 "
                    '`["glm-4.6-flash","glm-5.3"]`'
                ) from exc
            if isinstance(zhipu_models, list) and all(
                isinstance(model, str) and model.strip() for model in zhipu_models
            ):
                zhipu_models = {model: model for model in zhipu_models}
            elif isinstance(zhipu_models, dict) and all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in zhipu_models.items()
            ):
                # 兼容旧版 key -> model_id 字典格式。
                zhipu_models = dict(zhipu_models)
            else:
                raise RuntimeError(
                    "ZHIPU_MODELS 必须是 JSON 字符串列表，例如 "
                    '`["glm-4.6-flash","glm-5.3"]`'
                )
        else:
            # 兼容旧配置：只填写旧版单模型变量也可以启动。
            zhipu_models = {
                os.getenv("ZHIPU_MODEL_GLM_5_3", "glm-5.3"): os.getenv(
                    "ZHIPU_MODEL_GLM_5_3", "glm-5.3"
                )
            }

        return cls(
            logging=LoggingConfig(
                file=LogFileConfig(
                    enable=os.getenv("LOG_FILE_ENABLE", "true").lower() == "true",
                    level=os.getenv("LOG_FILE_LEVEL", "INFO"),
                    path=os.getenv("LOG_FILE_PATH", "logs"),
                    rotation=os.getenv("LOG_FILE_ROTATION", "10 MB"),
                    retention=os.getenv("LOG_FILE_RETENTION", "7 days"),
                ),
                console=LogConsoleConfig(
                    enable=os.getenv("LOG_CONSOLE_ENABLE", "true").lower() == "true",
                    level=os.getenv("LOG_CONSOLE_LEVEL", "INFO"),
                ),
            ),
            db_meta=DatabaseConfig(
                host=os.getenv("DB_META_HOST", "localhost"),
                port=int(os.getenv("DB_META_PORT", "3306")),
                user=os.environ["DB_META_USER"],
                password=os.environ["DB_META_PASSWORD"],
                database=os.environ["DB_META_DATABASE"],
            ),
            db_dw=DatabaseConfig(
                host=os.getenv("DB_DW_HOST", "localhost"),
                port=int(os.getenv("DB_DW_PORT", "3306")),
                user=os.environ["DB_DW_USER"],
                password=os.environ["DB_DW_PASSWORD"],
                database=os.environ["DB_DW_DATABASE"],
            ),
            qdrant=QdrantConfig(
                host=os.getenv("QDRANT_HOST", "localhost"),
                port=int(os.getenv("QDRANT_PORT", "6333")),
                embedding_size=int(os.getenv("QDRANT_EMBEDDING_SIZE", "1024")),
                metric_collection=os.getenv("QDRANT_METRIC_COLLECTION", "data_agent_metric"),
                column_collection=os.getenv("QDRANT_COLUMN_COLLECTION", "data_agent_column"),
            ),
            embedding=EmbeddingConfig(
                zhipu_api_key=os.environ["ZHIPU_API_KEY"],
                model_name=os.getenv("EMBEDDING_MODEL_NAME", "embedding-3"),
            ),
            es=ESConfig(
                host=os.getenv("ES_HOST", "localhost"),
                port=int(os.getenv("ES_PORT", "9200")),
                index_name=os.getenv("ES_INDEX_NAME", "data_agent"),
            ),
            llm=LLMConfig(
                api_key=os.environ["DEEPSEEK_API_KEY"],
                base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
                models=json.loads(os.getenv(
                    "DEEPSEEK_MODELS",
                    '["deepseek-v4-flash","deepseek-v4-pro"]',
                )),
            ),
            zhipu_llm=ZhipuLLMConfig(
                api_key=os.environ["ZHIPU_API_KEY"],
                base_url=os.getenv("ZHIPU_LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),
                models=zhipu_models,
            ),
        )


# 在配置模块首次导入时加载项目根目录的本机凭证文件。生产环境可直接注入环境变量。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

# 全局单例实例化暴露
app_config: AppConfig = AppConfig.load_from_env()

# 调用方未显式选择模型时使用的默认配置。
DEFAULT_LLM_PROVIDER = os.getenv("DEFAULT_LLM_PROVIDER", "deepseek")
DEFAULT_LLM_MODEL = os.getenv("DEFAULT_LLM_MODEL", "deepseek-v4-flash")



# ----------------- 3. 独立运行自检 -----------------
if __name__ == '__main__':
    print("🚀 正在验证 app_config.py 加载行为...")
    print("✅ 配置来源: 环境变量（本地可使用 .env）")
    print(f"✅ Qdrant 向量维度: {app_config.qdrant.embedding_size}")
    print(f"✅ 智谱 Embedding 模型: {app_config.embedding.model_name}")
    print(f"✅ LLM 基础模型: {app_config.llm.models}")
    print("\n🎉 app_config 配置模块校验 100% 成功，即插即用！")
