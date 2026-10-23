from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from uuid import uuid4

from app.service.mysql_conversation_repository import conversation_repository

router = APIRouter(prefix="/api/conversations", tags=["conversations"])

class CreateConversationRequest(BaseModel):
    conversation_id: str | None = Field(default=None, min_length=1, max_length=128)
    title: str = Field(default="", max_length=200)

@router.post("")
async def create_conversation(request: CreateConversationRequest):
    return conversation_repository.create_conversation(request.conversation_id or uuid4().hex, request.title)

@router.get("")
async def list_conversations():
    return {"items": conversation_repository.list_conversations()}

@router.get("/{conversation_id}")
async def get_conversation(conversation_id: str):
    item = conversation_repository.get_conversation(conversation_id)
    if item is None:
        raise HTTPException(404, "会话不存在")
    return item

@router.get("/{conversation_id}/messages")
async def list_messages(conversation_id: str):
    if conversation_repository.get_conversation(conversation_id) is None:
        raise HTTPException(404, "会话不存在")
    return {"items": conversation_repository.list_messages(conversation_id)}

@router.delete("/{conversation_id}")
async def delete_conversation(conversation_id: str):
    if not conversation_repository.delete_conversation(conversation_id):
        raise HTTPException(404, "会话不存在")
    return {"deleted": True, "conversation_id": conversation_id}
