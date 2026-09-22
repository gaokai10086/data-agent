"""
================================================================================
模块名称：Few-Shot 金标 SQL 质检与入库核心领域服务 🧠💾
文件路径：app/services/example_service.py
核心职责：
    1. 动态自适应查数仓 SHOW TABLES 并加锁内存缓存 (Schema Grounding)
    2. 基于 LLM-as-a-Judge 进行只读安全性、物理表对齐及语法有效性质检
    3. 自动提炼 pattern、tables、dialect、difficulty 元数据
    4. 对齐 SQLExampleQdrant (TypedDict) 与 BaseQdrantRepository 泛型仓储进行异步幂等持久化
================================================================================
"""
import re
import uuid
import json
import asyncio
from typing import List, Dict, Any
from fastapi import HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.llm import get_llm
from app.prompt.prompt_loader import load_prompt
from app.clients.mysql_client import dw_client_manager
from app.clients.qdrant_client import qdrant_client_manager
from app.clients.embedding_client import embedding_client_manager
from app.models.qdrant.sql_example_info_qdrant import SQLExampleQdrant
from app.repositories.qdrant.sql_example_repository import SQLExampleQdrantRepository
from app.core.logging import logger


class UserInputExampleItem(BaseModel):
    question: str = Field(..., min_length=3, description="业务分析提问")
    sql: str = Field(..., min_length=10, description="对应的金标 SQL")


class ExampleService:
    def __init__(self):
        self._cached_tables: List[str] = []
        self._cache_lock = asyncio.Lock()

    async def get_warehouse_tables(self) -> List[str]:
        """动态查询数仓所有真实存在的物理表（带双重检查内存缓存）"""
        if self._cached_tables:
            return self._cached_tables

        async with self._cache_lock:
            if self._cached_tables:
                return self._cached_tables

            try:
                if not dw_client_manager.engine:
                    dw_client_manager.init()

                async with AsyncSession(dw_client_manager.engine) as session:
                    result = await session.execute(text("SHOW TABLES;"))
                    tables = [row[0] for row in result.fetchall() if row and row[0]]
                    if tables:
                        self._cached_tables = sorted(tables)
                        logger.info(
                            f"✨ [ExampleService] 成功探测到数仓物理表 {len(self._cached_tables)} 张: {self._cached_tables}")
                        return self._cached_tables
            except Exception as e:
                logger.warning(f"⚠️ [ExampleService] 动态查表异常，启用标准底表兜底: {e}")

            self._cached_tables = [
                "fact_order", "fact_order_detail", "dim_product",
                "dim_promotion", "dim_store", "dim_user"
            ]
            return self._cached_tables

    async def audit_and_enrich_single(self, question: str, sql: str, warehouse_tables: List[str]) -> Dict[str, Any]:
        """单条样本大模型审查与打标"""
        llm = get_llm(provider="deepseek", model_tier="flash", temperature=0.0)
        prompt_template = load_prompt("audit_sql_example")
        tables_str = ", ".join([f"`{t}`" for t in warehouse_tables])

        try:
            rendered_prompt = prompt_template.format(
                question=question,
                sql=sql,
                warehouse_tables=tables_str
            )
            response = await llm.ainvoke(rendered_prompt)
            raw_text = response.content.strip()

            json_match = re.search(r"\{.*\}", raw_text, re.DOTALL)
            if not json_match:
                raise ValueError(f"大模型返回内容未包含合法 JSON: {raw_text[:100]}")

            return json.loads(json_match.group(0))
        except Exception as e:
            logger.error(f"❌ [ExampleService] 大模型评审执行或解析异常: {e}")
            return {
                "is_valid": False,
                "reason": f"智能质检服务异常或解析失败: {str(e)}"
            }

    async def batch_audit_and_upload(self, items: List[UserInputExampleItem]) -> Dict[str, Any]:
        """批量质检、打标并持久化入库主逻辑 (对齐 BaseQdrantRepository)"""
        if not items:
            raise HTTPException(status_code=400, detail="待上传列表不能为空")

        real_tables = await self.get_warehouse_tables()
        audited_payloads: List[SQLExampleQdrant] = []

        # 1. 批量审查与打标
        for idx, item in enumerate(items, start=1):
            audit_res = await self.audit_and_enrich_single(item.question, item.sql, real_tables)

            if not audit_res.get("is_valid", False):
                reason = audit_res.get("reason", "未通过数仓金标规范性审查")
                logger.warning(f"🚫 [ExampleService] 第 {idx} 条记录被拦截: {reason}")
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"【第 {idx} 条记录质检未通过】: {reason}"
                )

            # 按照 TypedDict 契约组装规范字典
            clean_sql = item.sql.strip().rstrip(";")
            payload_item: SQLExampleQdrant = {
                "id": "",  # 占位，在持久化时由 uuid5 确定性填充
                "question": item.question,
                "sql": clean_sql,
                "pattern": audit_res.get("pattern", "单表切片与基础聚合"),
                "tables": audit_res.get("tables", []),
                "dialect": audit_res.get("dialect", "mysql"),
                "difficulty": audit_res.get("difficulty", "简单 (单表聚合)")
            }
            audited_payloads.append(payload_item)

        # 2. 向量化并委托 BaseQdrantRepository 仓储规范持久化
        try:
            embedding_client = embedding_client_manager.client
            repo = SQLExampleQdrantRepository(qdrant_client_manager.client)

            # 自动初始化创建底层集合
            await repo.ensure_collection()

            ids: List[str] = []
            embeddings: List[List[float]] = []

            for p in audited_payloads:
                # 采用确定性 UUIDv5 保证相同 (question + sql) 重复导入时的幂等性
                point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{p['question']}_{p['sql']}"))
                p["id"] = point_id

                # 对齐项目中统一使用的向量化接口
                vector = embedding_client.embed_query(p["question"])

                ids.append(point_id)
                embeddings.append(vector)

            # 调用基类泛型仓储的异步批处理 upsert
            await repo.upsert(
                ids=ids,
                embeddings=embeddings,
                payloads=audited_payloads,
                batch_size=10
            )

            logger.info(f"✨ [ExampleService] 成功通过 BaseQdrantRepository 落库 {len(ids)} 条金标样本")

            return {
                "code": 200,
                "message": f"🎉 质检全量通过！已结合数仓真实表完成审查，成功规范入库 {len(ids)} 个金标示例。",
                "admitted_count": len(ids)
            }
        except Exception as e:
            logger.error(f"❌ [ExampleService] 仓储入库异常: {e}")
            raise HTTPException(status_code=500, detail=str(e))


# 全局单例导出
example_service = ExampleService()