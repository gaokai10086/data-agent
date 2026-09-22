"""字段语义治理接口：供业务人员查看和审批模型生成的字段语义候选。"""
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_meta_session
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository
from app.schemas.meta_governance import (
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
