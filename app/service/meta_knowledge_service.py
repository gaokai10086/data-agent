"""
================================================================================
服务名称：元数据知识库自动化构建服务 (生产级自愈与弹性管道版) 🧠
文件路径：app/service/meta_knowledge_service.py
核心改进：
    1. 引入两段式语法解析与清洗管道，彻底根治 physical_name 与 fields 引发的 Pydantic 降级
    2. 对 MetricInfoMySQL 采用动态自省实例化，彻底根治 TypeError: 'formula' 关键字异常
    3. 全链路显式生成注入 32 位 UUID，避免 Session.flush 状态机主键缺失
================================================================================
"""
from typing import Any
import uuid
import json
from langchain_core.embeddings import Embeddings
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from pydantic import BaseModel, Field, AliasChoices
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as rest_models
from elasticsearch import AsyncElasticsearch

from app.core.datasource.base import BaseDatasourceConnector, TableProfile
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository
from app.models.mysql.table_info_mysql import TableInfoMySQL
from app.models.mysql.column_info_mysql import ColumnInfoMySQL
from app.models.mysql.metric_info_mysql import MetricInfoMySQL
from app.agent.llm import get_llm
from app.core.logging import logger


# ==================== 1. 结构化反序列化契约模型 ====================
class ColumnSemanticProfile(BaseModel):
    name: str = Field(
        validation_alias=AliasChoices("name", "physical_name", "column_name", "col_name"),
        description="物理列名"
    )
    chinese_name: str = Field(
        default="",
        validation_alias=AliasChoices("chinese_name", "logical_name", "business_name", "cn_name"),
        description="推导出的精准中文业务名称"
    )
    description: str = Field(
        default="",
        validation_alias=AliasChoices("description", "desc", "comment", "meaning"),
        description="针对该字段详细的业务口径与用途说明"
    )
    alias: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("alias", "aliases", "synonyms", "business_terms"),
        description="业务人员常用的口头语、同义词或简称"
    )


class TableSemanticProfile(BaseModel):
    table_name: str = Field(
        default="",
        validation_alias=AliasChoices("table_name", "name", "physical_table_name"),
        description="物理表名"
    )
    table_comment: str = Field(
        default="",
        validation_alias=AliasChoices("table_comment", "description", "table_desc", "comment"),
        description="整张表的中文业务功能定位说明"
    )
    columns: list[ColumnSemanticProfile] = Field(
        default_factory=list,
        validation_alias=AliasChoices("columns", "fields", "column_list", "column_profiles"),
        description="字段画像扩充列表"
    )


SEMANTIC_ENRICH_PROMPT = """你是一个世界顶级的企业数仓架构师与数据资产治理专家。
请结合物理表名、物理列名、数据类型以及真实数据样例，推导精准的中文业务定义与业务同义词（别名）。

【物理表名】: {table_name}
【原始表注释】: {table_comment}
【字段画像及真实数据样例】:
{columns_context}

【严格输出规范】:
请输出且仅输出纯净的 JSON 格式，严禁包含任何 Markdown 标记（不要出现 ```json）。格式范式如下：
{{
  "table_name": "{table_name}",
  "table_comment": "表的核心业务功能总结",
  "columns": [
    {{
      "name": "物理列名(必须与数仓原列名100%一致)",
      "chinese_name": "精准中文业务名称",
      "description": "详细业务用途与口径说明",
      "alias": ["业务俗称1", "业务俗称2", "业务俗称3"]
    }}
  ]
}}
"""


class MetaKnowledgeService:
    def __init__(
        self,
        connector: BaseDatasourceConnector,
        meta_mysql_repository: MetaMySQLRepository,
        embedding_client: Embeddings,
        qdrant_client: AsyncQdrantClient,
        es_client: AsyncElasticsearch,
        column_collection_name: str = "data_agent_column",
        metric_collection_name: str = "data_agent_metric",
        es_index_name: str = "data_agent"
    ):
        self.connector = connector
        self.meta_repo = meta_mysql_repository
        self.embedding = embedding_client
        self.qdrant = qdrant_client
        self.es = es_client
        self.col_collection = column_collection_name
        self.metric_collection = metric_collection_name
        self.es_index = es_index_name

    async def _init_qdrant_collections(self, vector_size: int = 1024):
        for c_name in [self.col_collection, self.metric_collection]:
            exists = await self.qdrant.collection_exists(c_name)
            if not exists:
                logger.info(f"🔄 [Qdrant] 集合 [{c_name}] 不存在，正在自动建立...")
                await self.qdrant.create_collection(
                    collection_name=c_name,
                    vectors_config=rest_models.VectorParams(
                        size=vector_size,
                        distance=rest_models.Distance.COSINE
                    )
                )
                logger.info(f"✅ [Qdrant] 集合 [{c_name}] 建立成功")

    async def _init_es_index(self):
        exists = await self.es.indices.exists(index=self.es_index)
        if not exists:
            logger.info(f"🔄 [ES] 索引 [{self.es_index}] 不存在，正在自动建立...")
            await self.es.indices.create(
                index=self.es_index,
                mappings={
                    "properties": {
                        "table_name": {"type": "keyword"},
                        "column_name": {"type": "keyword"},
                        "value": {"type": "text", "analyzer": "standard", "fields": {"keyword": {"type": "keyword"}}}
                    }
                }
            )
            logger.info(f"✅ [ES] 索引 [{self.es_index}] 建立成功")

    async def _enrich_table_semantics(self, profile: TableProfile) -> TableSemanticProfile:
        logger.info(f"🤖 正在调用大模型为表 [{profile.table_name}] 逆向挖掘中文语义与业务同义词...")

        cols_lines = [
            f"- 列名: {c.name} | 类型: {c.data_type} | 原注释: {c.comment} | 样例: {c.sample_values[:3]}"
            for c in profile.columns
        ]

        prompt = PromptTemplate(
            template=SEMANTIC_ENRICH_PROMPT,
            input_variables=["table_name", "table_comment", "columns_context"]
        )
        llm = get_llm(provider="deepseek", model_tier="flash", temperature=0.1, streaming=False)
        # 解耦：只进行 JSON 语法解析，由 Python 管道做清洗与模型反序列化
        chain = prompt | llm | JsonOutputParser()

        try:
            enrichment_dict = await chain.ainvoke({
                "table_name": profile.table_name,
                "table_comment": profile.comment,
                "columns_context": "\n".join(cols_lines)
            })

            # 1. 解开大模型可能出现的表名顶层嵌套
            if isinstance(enrichment_dict, dict):
                if profile.table_name in enrichment_dict and isinstance(enrichment_dict[profile.table_name], dict):
                    enrichment_dict = enrichment_dict[profile.table_name]

            # 2. 字段名归一化清洗管道：将 physical_name/column_name 统一为 name
            raw_cols = enrichment_dict.get("columns") or enrichment_dict.get("fields") or []
            normalized_cols = []
            for item in raw_cols:
                if isinstance(item, dict):
                    col_name = item.get("name") or item.get("physical_name") or item.get("column_name") or ""
                    cn_name = item.get("chinese_name") or item.get("logical_name") or item.get("business_name") or col_name
                    desc = item.get("description") or item.get("desc") or item.get("comment") or cn_name
                    aliases = item.get("alias") or item.get("aliases") or item.get("synonyms") or []
                    if isinstance(aliases, str):
                        aliases = [aliases]
                    normalized_cols.append(ColumnSemanticProfile(
                        name=col_name,
                        chinese_name=cn_name,
                        description=desc,
                        alias=aliases
                    ))

            table_comment = (
                enrichment_dict.get("table_comment")
                or enrichment_dict.get("description")
                or profile.comment
                or profile.table_name
            )
            enriched_profile = TableSemanticProfile(
                table_name=profile.table_name,
                table_comment=table_comment,
                columns=normalized_cols
            )
            logger.info(f"✨ 表 [{profile.table_name}] 语义重构完成: {enriched_profile.table_comment}")
            return enriched_profile

        except Exception as e:
            logger.warning(f"⚠️ 大模型语义扩充触发安全降级: {e}")
            fallback_cols = [
                ColumnSemanticProfile(
                    name=c.name,
                    chinese_name=c.comment if c.comment else c.name,
                    description=c.comment if c.comment else c.name,
                    alias=[c.name]
                ) for c in profile.columns
            ]
            return TableSemanticProfile(
                table_name=profile.table_name,
                table_comment=profile.comment if profile.comment else profile.table_name,
                columns=fallback_cols
            )

    async def auto_build_all(self, custom_metrics: list[dict[str, Any]] | None = None):
        logger.info("🚀 开始启动带有大模型自动语义扩充的全量构建流程...")

        await self._init_qdrant_collections()
        await self._init_es_index()

        table_names = await self.connector.get_all_table_names()
        if not table_names:
            logger.warning("⚠️ 数仓中未检测到任何物理表！")
            return

        qdrant_points = []
        point_id = 1

        for tbl_name in table_names:
            profile: TableProfile = await self.connector.get_table_profile(tbl_name)

            for col in profile.columns:
                if any(t in col.data_type for t in ["CHAR", "TEXT", "VARCHAR"]):
                    col.sample_values = await self.connector.sample_column_values(tbl_name, col.name, limit=10)

            enriched_meta: TableSemanticProfile = await self._enrich_table_semantics(profile)

            # 显式生成并存入 TableInfo 实体
            table_record = await self.meta_repo.get_table_by_name(tbl_name)
            if not table_record:
                table_record = TableInfoMySQL(
                    id=uuid.uuid4().hex,
                    name=tbl_name,
                    role="fact" if "fact" in tbl_name.lower() else "dimension",
                    description=enriched_meta.table_comment
                )
                await self.meta_repo.save_table(table_record)
            else:
                table_record.description = enriched_meta.table_comment
                await self.meta_repo.save_table(table_record)

            enriched_col_map = {c.name: c for c in enriched_meta.columns}

            for col in profile.columns:
                enrich_info = enriched_col_map.get(col.name)
                chinese_desc = enrich_info.description if enrich_info and enrich_info.description else (
                    col.comment if col.comment else col.name)
                aliases = enrich_info.alias if enrich_info and enrich_info.alias else []

                # 显式生成并存入 ColumnInfo 实体
                col_record = await self.meta_repo.get_column(table_record.id, col.name)
                col_role = "primary_key" if col.is_primary_key else (
                    "dimension" if any(t in col.data_type for t in ["CHAR", "DATE", "TIME"]) else "measure"
                )
                if not col_record:
                    col_record = ColumnInfoMySQL(
                        id=uuid.uuid4().hex,
                        table_id=table_record.id,
                        name=col.name,
                        type=col.data_type,
                        role=col_role,
                        description=chinese_desc,
                        alias=aliases,
                        examples=[str(v) for v in col.sample_values[:3]]
                    )
                    await self.meta_repo.save_column(col_record)
                else:
                    col_record.description = chinese_desc
                    col_record.alias = aliases
                    col_record.examples = [str(v) for v in col.sample_values[:3]]
                    await self.meta_repo.save_column(col_record)

                # 枚举样本灌入 ES 倒排索引
                for val in col.sample_values:
                    if val:
                        await self.es.index(
                            index=self.es_index,
                            document={
                                "table_name": tbl_name,
                                "column_name": col.name,
                                "value": str(val)
                            }
                        )

                # 构建语义文本并写入 Qdrant 向量列表
                alias_str = ", ".join(aliases) if aliases else "无"
                semantic_rich_text = (
                    f"表名: {tbl_name} ({enriched_meta.table_comment}), "
                    f"字段名: {col.name}, 中文业务含义: {chinese_desc}, "
                    f"业务常用别名同义词: [{alias_str}], 物理类型: {col.data_type}"
                )

                embedding_vector = await self.embedding.aembed_query(semantic_rich_text)
                qdrant_points.append(
                    rest_models.PointStruct(
                        id=point_id,
                        vector=embedding_vector,
                        payload={
                            "table_id": table_record.id,
                            "table_name": tbl_name,
                            "column_id": col_record.id,
                            "column_name": col.name,
                            "comment": chinese_desc,
                            "alias": aliases,
                            "data_type": col.data_type
                        }
                    )
                )
                point_id += 1

        # 批量刷入 Qdrant
        if qdrant_points:
            logger.info(f"⏳ 正在向 Qdrant 批量刷新 {len(qdrant_points)} 个中文语义特征点位...")
            await self.qdrant.upsert(
                collection_name=self.col_collection,
                points=qdrant_points
            )
            logger.info("✅ Qdrant 字段语义特征批量入库完成！")

        # 处理自定义指标 (防御性动态组装，彻底杜绝 TypeError)
        if custom_metrics:
            logger.info(f"📊 正在处理 {len(custom_metrics)} 个核心业务分析指标...")
            metric_points = []
            m_id = 1
            for m in custom_metrics:
                m_name = m.get("name")
                m_desc = m.get("description", "")
                m_formula = m.get("formula", "")
                m_alias = m.get("alias", [])

                metric_record = await self.meta_repo.get_metric_by_name(m_name)
                if not metric_record:
                    # 动态属性自省构造
                    init_kwargs = {
                        "id": uuid.uuid4().hex,
                        "name": m_name,
                        "description": m_desc,
                        "alias": m_alias
                    }
                    if hasattr(MetricInfoMySQL, "formula"):
                        init_kwargs["formula"] = m_formula
                    if hasattr(MetricInfoMySQL, "relevant_columns"):
                        init_kwargs["relevant_columns"] = m.get("relevant_columns", [])

                    metric_record = MetricInfoMySQL(**init_kwargs)
                    await self.meta_repo.save_metric(metric_record)

                m_vector = await self.embedding.aembed_query(
                    f"指标名: {m_name}, 口径含义: {m_desc}, 别名: {m_alias}, 计算公式: {m_formula}"
                )
                metric_points.append(
                    rest_models.PointStruct(
                        id=m_id,
                        vector=m_vector,
                        payload={
                            "metric_id": metric_record.id,
                            "metric_name": m_name,
                            "description": m_desc,
                            "formula": m_formula
                        }
                    )
                )
                m_id += 1

            if metric_points:
                await self.qdrant.upsert(
                    collection_name=self.metric_collection,
                    points=metric_points
                )
                logger.info("✅ Qdrant 业务核心指标向量入库完毕！")

        logger.info("🎉 中文数仓资产语义挖掘与知识库同步圆满完成！")