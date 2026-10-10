"""
================================================================================
模块名称：Few-Shot 金标 SQL 资产录入与严格准入审计接口 💾🛡️
文件路径：app/api/routers/example_router.py
核心职责：
    1. 极简入参：仅接收 question 与 sql
    2. 严格白名单：直接拉取数仓真实物理表，失败立即熔断，严禁伪造兜底名单
    3. 生产级审计：调用 load_prompt("data_agent/audit_sql_example") 驱动 LLM 严格审核
    4. 准入与落库：is_valid=True 则热写入 Qdrant；不合规或系统异常则坚决拦截
================================================================================
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List
import uuid

from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import JsonOutputParser

from app.clients.embedding_client import embedding_client_manager
from app.clients.qdrant_client import qdrant_client_manager
from app.clients.mysql_client import meta_client_manager
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository
from app.repositories.qdrant.sql_example_repository import SQLExampleQdrantRepository
from app.models.qdrant.sql_example_info_qdrant import SQLExampleQdrant
from app.core.llm import llm
from app.prompt.prompt_loader import load_prompt
from app.core.logging import logger

example_router = APIRouter(prefix="/sql-example", tags=["SQL金标资产管理"])


class SQLExampleCreateRequest(BaseModel):
    """前端极简录入请求体"""
    question: str = Field(..., description="业务自然语言提问")
    sql: str = Field(..., description="标准执行 SQL 语句")


class SQLExampleAuditResult(BaseModel):
    """大模型质量审计与元数据打标契约"""
    is_valid: bool = Field(description="是否通过质量安全与物理表对齐审核")
    reason: str = Field(description="审核通过理由或拦截具体原因")
    pattern: str = Field(default="基础数据统计", description="核心计算模式")
    tables: List[str] = Field(default_factory=list, description="真实的数仓物理底表名列表")
    dialect: str = Field(default="mysql", description="方言")
    difficulty: str = Field(default="中等", description="复杂度等级")


async def _get_warehouse_table_whitelist() -> List[str]:
    """
    从数仓元数据表动态拉取已注册的物理表白名单
    遵循 Fail-Fast 原则：数据库连接失败或无表时直接中断，坚决杜绝静态硬编码兜底！
    """
    try:
        if not getattr(meta_client_manager, "client", None):
            meta_client_manager.init()
        meta_repo = MetaMySQLRepository(meta_client_manager.client)
        table_infos = await meta_repo.get_all_table_infos()

        table_names = [t.name for t in table_infos if getattr(t, "name", None)]
        if not table_names:
            logger.error("❌ 数仓元数据中未检索到任何已注册物理表，白名单为空！")
            raise RuntimeError("数仓物理表元数据未初始化或记录为空")

        logger.info(f"📋 [SQL-Audit] 成功加载数仓物理表安全白名单 (共 {len(table_names)} 张): {table_names}")
        return table_names

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 读取数仓物理表元数据异常: {e}")
        # 抛出标准 HTTP 500 异常，前端直接显示基础设施故障
        raise HTTPException(
            status_code=500,
            detail=f"数仓元数据库异常，无法获取真实物理表白名单，准入安全审计中断: {str(e)}"
        )


@example_router.post("/upload")
async def upload_sql_example(req: SQLExampleCreateRequest):
    """在线上传、安全审计与向量化金标 SQL 样本"""
    clean_question = req.question.strip()
    clean_sql = req.sql.strip().rstrip(";")

    # 1. 获取物理表安全白名单 (失败直接抛出 500 异常中止流程)
    warehouse_tables = await _get_warehouse_table_whitelist()

    # 2. 载入独立提示词模板执行大模型安全审计
    try:
        audit_template = load_prompt("data_agent/audit_sql_example")
        prompt = PromptTemplate(
            template=audit_template,
            input_variables=["question", "sql", "warehouse_tables"]
        )
        parser = JsonOutputParser(pydantic_object=SQLExampleAuditResult)
        chain = prompt | llm | parser

        audit_dict = await chain.ainvoke({
            "question": clean_question,
            "sql": clean_sql,
            "warehouse_tables": warehouse_tables
        })
        audit_result = SQLExampleAuditResult(**audit_dict)
        logger.info(f"🧐 [SQL-Audit] 审计评判: is_valid={audit_result.is_valid}, reason={audit_result.reason}")

    except Exception as e:
        logger.error(f"❌ 大模型审计推理异常: {e}")
        raise HTTPException(status_code=500, detail=f"大模型安全准入审查服务调用异常: {str(e)}")

    # 3. 业务拦截门禁：若未通过安全与质量审核，抛出 400 阻断
    if not audit_result.is_valid:
        logger.warning(f"🚫 [SQL-Audit] 拦截不合规 SQL 样本: {audit_result.reason}")
        raise HTTPException(
            status_code=400,
            detail=f"【安全与质量审核未通过】: {audit_result.reason}"
        )

    # 4. 审核通过：执行向量化并热入库 Qdrant
    try:
        if not getattr(embedding_client_manager, "client", None):
            embedding_client_manager.init()
        if not getattr(qdrant_client_manager, "client", None):
            qdrant_client_manager.init()

        embedding_client = embedding_client_manager.client
        repo = SQLExampleQdrantRepository(qdrant_client_manager.client)

        point_id = str(uuid.uuid4())
        vector = embedding_client.embed_query(clean_question)

        example_payload: SQLExampleQdrant = {
            "id": point_id,
            "question": clean_question,
            "sql": clean_sql,
            "pattern": audit_result.pattern,
            "tables": audit_result.tables,
            "dialect": audit_result.dialect,
            "difficulty": audit_result.difficulty,
            "score": 1.0
        }

        await repo.save_examples_with_vectors([(example_payload, vector)])
        logger.info(f"🎉 成功录入金标资产: [{audit_result.pattern}] [底表: {audit_result.tables}] - {clean_question}")

        return {
            "code": 200,
            "message": "金标样本已通过安全审计，并成功向量化热录入知识库！",
            "data": {
                "id": point_id,
                "reason": audit_result.reason,
                "pattern": audit_result.pattern,
                "tables": audit_result.tables,
                "difficulty": audit_result.difficulty
            }
        }
    except Exception as e:
        logger.error(f"❌ Qdrant 写入异常: {e}")
        raise HTTPException(status_code=500, detail=f"向量入库异常: {str(e)}")
