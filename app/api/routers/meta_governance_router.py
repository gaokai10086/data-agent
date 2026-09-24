"""字段语义治理接口：供业务人员查看和审批模型生成的字段语义候选。"""
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_dw_session, get_meta_session
from app.repositories.mysql.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository
from app.schemas.meta_governance import (
    SemanticFieldUpdateRequest,
    SemanticFieldView,
    SemanticDraftApproveRequest,
    SemanticDraftRejectRequest,
    SemanticDraftView,
)
from app.service.column_semantic_draft_service import ColumnSemanticDraftService

router = APIRouter(prefix="/api/meta-governance", tags=["字段语义治理"])


def _build_service(session) -> ColumnSemanticDraftService:
    """审核流程只访问 Meta，不初始化 DW、Embedding 或 LLM。"""
    return ColumnSemanticDraftService(
        meta_repository=MetaMySQLRepository(session),
        connector=None,
        llm=None,
    )


@router.get("/semantic-drafts")
async def list_semantic_drafts(
    status: str = Query("pending", description="pending、approved、rejected 或 all"),
    session=Depends(get_meta_session),
) -> dict:
    """列出字段语义草稿，供审批页面使用。"""
    if status not in {"pending", "approved", "rejected", "all"}:
        raise HTTPException(status_code=422, detail="status 只支持 pending、approved、rejected 或 all")
    service = _build_service(session)
    views = await service.list_drafts(None if status == "all" else status)
    return {"items": [SemanticDraftView.model_validate(view).model_dump() for view in views]}


@router.get("/fields")
async def list_fields(
    table_name: str = Query("", description="按表名过滤，留空返回全部字段"),
    session=Depends(get_meta_session),
) -> dict:
    """列出已发布字段语义，支持按表筛选。"""
    service = _build_service(session)
    views = await service.list_fields(table_name.strip() or None)
    return {"items": [SemanticFieldView.model_validate(view).model_dump() for view in views]}


@router.post("/fields/{column_id}/update")
async def update_field(
    column_id: str,
    request: SemanticFieldUpdateRequest,
    session=Depends(get_meta_session),
) -> dict:
    """直接修改已发布字段语义，并留下人工修改审计记录。"""
    service = _build_service(session)
    try:
        report = await service.update_field(
            column_id=column_id,
            reviewer=request.reviewer,
            review_comment=request.review_comment,
            description=request.description,
            alias=request.alias,
            examples=request.examples,
            role=request.role,
            value_type=request.value_type,
        )
        await session.commit()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "column_id": column_id,
        "qualified_name": report.qualified_name,
        "reviewer": report.reviewer,
        "published": report.published,
        "previous": report.previous,
    }


@router.get("/fields/{column_id}/value-diff")
async def field_value_diff(
    column_id: str,
    limit: int = Query(200, ge=1, le=1000, description="从 DW 最多读取多少个去重值"),
    meta_session=Depends(get_meta_session),
    dw_session=Depends(get_dw_session),
) -> dict:
    """对比 Meta 已登记的枚举值与 DW 实际取值，帮助发现未登记的新值。"""
    repository = MetaMySQLRepository(meta_session)
    column = await repository.get_column_by_id(column_id)
    if column is None:
        raise HTTPException(status_code=404, detail=f"未找到字段 [{column_id}]")
    table = await repository.get_table_by_id(column.table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="字段所属表不存在")

    try:
        dw_values = await DWMySQLRepository(dw_session).get_column_values(
            table.name, column.name or "", limit
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"读取 DW 取值失败：{exc}") from exc

    diff = ColumnSemanticDraftService.build_value_diff(column, dw_values)
    return {
        "column_id": column_id,
        "qualified_name": f"{table.name}.{column.name}",
        **diff,
    }


@router.post("/semantic-drafts/{draft_id}/approve")
async def approve_semantic_draft(
    draft_id: str,
    request: SemanticDraftApproveRequest,
    session=Depends(get_meta_session),
) -> dict:
    """审核通过并把内容发布回 column_info。"""
    service = _build_service(session)
    try:
        report = await service.approve(
            draft_id=draft_id,
            reviewer=request.reviewer,
            review_comment=request.review_comment,
            description=request.description,
            alias=request.alias,
            value_examples=request.examples,
            role=request.role,
        )
        await session.commit()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "draft_id": report.draft_id,
        "qualified_name": report.qualified_name,
        "status": report.status,
        "reviewer": report.reviewer,
        "published": report.published,
        "previous": report.previous,
    }


@router.post("/semantic-drafts/{draft_id}/reject")
async def reject_semantic_draft(
    draft_id: str,
    request: SemanticDraftRejectRequest,
    session=Depends(get_meta_session),
) -> dict:
    """审核驳回，column_info 保持原样。"""
    service = _build_service(session)
    try:
        report = await service.reject(
            draft_id=draft_id,
            reviewer=request.reviewer,
            review_comment=request.review_comment,
        )
        await session.commit()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "draft_id": report.draft_id,
        "qualified_name": report.qualified_name,
        "status": report.status,
        "reviewer": report.reviewer,
    }
