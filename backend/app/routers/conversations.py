"""对话 CRUD — GET/POST/PATCH/DELETE /api/conversations"""

import json
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.core.errors import AppError

from app.core.types import Conversation, ChatMessage

logger = logging.getLogger(__name__)
router = APIRouter()


class AppendRequest(BaseModel):
    role: str
    content: str


class RenameRequest(BaseModel):
    title: str


@router.get("/conversations")
def list_conversations(request: Request):
    store = request.app.state.store
    return store.list_conversations()


@router.get("/conversations/{conv_id}")
def get_conversation(conv_id: str, request: Request):
    store = request.app.state.store
    conv = store.get_conversation(conv_id)
    if not conv:
        raise AppError("CONV_NOT_FOUND", "对话不存在", status_code=404)
    return conv


@router.post("/conversations")
def create_conversation(title: str = "", request: Request = None):
    store = request.app.state.store
    conv = Conversation(
        id=f"conv_{uuid.uuid4().hex[:12]}",
        title=title or "新对话",
    )
    store.save_conversation(conv)
    return conv.to_dict()


@router.patch("/conversations/{conv_id}/append")
def append_message(conv_id: str, data: AppendRequest, request: Request):
    """追加消息到对话"""
    store = request.app.state.store
    conv = store.get_conversation(conv_id)
    if not conv:
        raise AppError("CONV_NOT_FOUND", "对话不存在", status_code=404)

    msg = ChatMessage(
        id=f"msg_{uuid.uuid4().hex[:12]}",
        role=data.role,
        content=data.content,
    )
    conv.setdefault("messages", []).append(msg.to_dict())
    conv["updated_at"] = datetime.now().isoformat()

    # 首条用户消息作为标题
    if conv["title"] == "新对话" and data.role == "user":
        conv["title"] = data.content[:60] + ("..." if len(data.content) > 60 else "")

    store.save_conversation(conv)
    return {"status": "ok", "id": msg.id}


@router.patch("/conversations/{conv_id}/rename")
def rename_conversation(conv_id: str, data: RenameRequest, request: Request):
    store = request.app.state.store
    ok = store.rename_conversation(conv_id, data.title)
    return {"status": "ok" if ok else "not found"}


@router.delete("/conversations/{conv_id}")
def delete_conversation(conv_id: str, request: Request):
    store = request.app.state.store
    store.delete_conversation(conv_id)
    return {"status": "deleted"}
