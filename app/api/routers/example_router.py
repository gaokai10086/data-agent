"""
================================================================================
模块名称：Few-Shot 金标 SQL 资产录入与运维接口 💾
文件路径：app/api/routers/example_router.py
================================================================================
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional
import uuid

from app.clients.embedding_client import embedding_client_manager
from app.clients.qdrant_client import qdrant_client_manager
from app.api.deps import get_dw_repository
from app.repositories.qdrant.sql_example_repository import SQLExampleQdrantRepository
from app.repositories.mysql.dw_mysql_repository import DWMySQLRepository
from app.models.qdrant.sql_example_info_qdrant import (
    CALCULATION_PATTERNS,
    SQL_DIALECTS,
    SQLExampleQdrant,
)
from app.core.logging import logger
from app.service.sql_validation_service import SQLValidationService

example_router = APIRouter(prefix="/api/sql-example", tags=["SQL金标资产管理"])


class SQLExampleCreateRequest(BaseModel):
    """录入金标 SQL 请求体"""
    question: str = Field(..., description="业务自然语言提问")
    sql: str = Field(..., description="标准标准执行 SQL 语句")
    pattern: str = Field(default=CALCULATION_PATTERNS[0], description="计算范式分类")
    tables: List[str] = Field(default_factory=list, description="涉及底表清单；已由系统自动解析，保留兼容旧客户端")
    difficulty: str = Field(default="中等", description="复杂度评级")
    dialect: str = Field(default="mysql", description="方言")


def _init_example_clients():
    """安全初始化金标 SQL 所需的 Embedding 与 Qdrant 客户端。"""
    if not getattr(embedding_client_manager, "client", None):
        embedding_client_manager.init()
    if not getattr(qdrant_client_manager, "client", None):
        qdrant_client_manager.init()
    return embedding_client_manager.client, SQLExampleQdrantRepository(qdrant_client_manager.client)


async def _validate_and_extract_tables(
    req: SQLExampleCreateRequest, dw_repository: DWMySQLRepository
) -> list[str]:
    """校验 SQL 与计算范式，并通过 EXPLAIN 解析 SQL 实际引用的物理表。"""
    try:
        if req.pattern not in CALCULATION_PATTERNS:
            raise ValueError(f"计算范式 [{req.pattern}] 不在允许范围内")
        if req.dialect not in SQL_DIALECTS:
            raise ValueError(f"SQL 方言 [{req.dialect}] 不在当前支持范围内")
        validator = SQLValidationService(dw_repository=dw_repository)
        await validator.validate_read_only_sql(req.sql)
        return await dw_repository.extract_tables_from_sql(req.sql)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"SQL 校验失败：{exc}") from exc


@example_router.post("/upload")
async def upload_sql_example(
    req: SQLExampleCreateRequest,
    dw_repository: DWMySQLRepository = Depends(get_dw_repository),
):
    """在线上传并向量化金标 SQL 样本"""
    try:
        tables = await _validate_and_extract_tables(req, dw_repository)
        embedding_client, repo = _init_example_clients()

        # 2. 计算向量并生成唯一 ID
        point_id = str(uuid.uuid4())
        vector = embedding_client.embed_query(req.question)

        # 3. 构造落库 Payload
        example_payload: SQLExampleQdrant = {
            "id": point_id,
            "question": req.question.strip(),
            "sql": req.sql.strip().rstrip(";"),
            "pattern": req.pattern,
            "tables": tables,
            "dialect": req.dialect,
            "difficulty": req.difficulty,
            "score": 1.0
        }

        # 4. 热写入 Qdrant 集合
        await repo.save_examples_with_vectors([(example_payload, vector)])
        logger.info(f"🎉 成功录入金标案例: [{req.pattern}] {req.question}")

        return {
            "code": 200,
            "message": "金标样本成功入库并建立向量索引！",
            "data": {"id": point_id}
        }
    except Exception as e:
        logger.error(f"❌ 录入金标样本异常: {e}")
        raise HTTPException(status_code=500, detail=f"录入失败: {str(e)}")


@example_router.get("/list")
async def list_sql_examples():
    """列出金标 SQL 样例，供元数据管理页面使用。"""
    try:
        if not getattr(qdrant_client_manager, "client", None):
            qdrant_client_manager.init()
        repo = SQLExampleQdrantRepository(qdrant_client_manager.client)
        items = await repo.list_examples()
        return {"items": items}
    except Exception as e:
        logger.error(f"❌ 读取金标样本异常: {e}")
        raise HTTPException(status_code=500, detail=f"读取失败: {str(e)}") from e


@example_router.put("/{example_id}")
async def update_sql_example(
    example_id: str,
    req: SQLExampleCreateRequest,
    dw_repository: DWMySQLRepository = Depends(get_dw_repository),
):
    """修改一条金标 SQL；问题文本变化时会重新计算向量。"""
    try:
        tables = await _validate_and_extract_tables(req, dw_repository)
        embedding_client, repo = _init_example_clients()
        vector = embedding_client.embed_query(req.question.strip())
        payload: SQLExampleQdrant = {
            "id": example_id,
            "question": req.question.strip(),
            "sql": req.sql.strip().rstrip(";"),
            "pattern": req.pattern,
            "tables": tables,
            "dialect": req.dialect,
            "difficulty": req.difficulty,
            "score": 1.0,
        }
        await repo.save_examples_with_vectors([(payload, vector)])
        return {"code": 200, "message": "金标样本已更新", "data": {"id": example_id}}
    except Exception as e:
        logger.error(f"❌ 更新金标样本异常: {e}")
        raise HTTPException(status_code=500, detail=f"更新失败: {str(e)}") from e


@example_router.delete("/{example_id}")
async def delete_sql_example(example_id: str):
    """删除一条金标 SQL 样例。"""
    try:
        _, repo = _init_example_clients()
        await repo.delete_example(example_id)
        return {"code": 200, "message": "金标样本已删除", "data": {"id": example_id}}
    except Exception as e:
        logger.error(f"❌ 删除金标样本异常: {e}")
        raise HTTPException(status_code=500, detail=f"删除失败: {str(e)}") from e
