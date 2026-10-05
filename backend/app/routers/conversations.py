"""对话 CRUD — GET/POST/PATCH/DELETE /api/conversations"""

import json
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.errors import AppError
from app.core.dependencies import get_store
from app.core.types import Conversation, ChatMessage
from app.agent.session import SessionManager

logger = logging.getLogger(__name__)
router = APIRouter()


class AppendRequest(BaseModel):
    role: str
    content: str


class RenameRequest(BaseModel):
    title: str


class PatchRequest(BaseModel):
    archived: bool | None = None


class RebuildRequest(BaseModel):
    """全量重建消息列表 — 编辑/删除/分支截断统一走此接口（前端算好新消息数组）"""
    messages: list[dict]


class ClearContextRequest(BaseModel):
    """清除上下文边界（C4）：该索引前的消息不再携带进上下文、不参与压缩"""
    index: int


@router.get("/conversations")
def list_conversations(store=Depends(get_store)):
    return store.list_conversations()


@router.get("/conversations/{conv_id}")
def get_conversation(conv_id: str, store=Depends(get_store)):
    conv = store.get_conversation(conv_id)
    if not conv:
        raise AppError("CONV_NOT_FOUND", "对话不存在", status_code=404)
    return conv


@router.post("/conversations")
def create_conversation(title: str = "", store=Depends(get_store)):
    conv = Conversation(
        id=f"conv_{uuid.uuid4().hex[:12]}",
        title=title or "新对话",
    )
    store.save_conversation(conv)
    return conv.to_dict()


@router.patch("/conversations/{conv_id}/append")
def append_message(conv_id: str, data: AppendRequest, store=Depends(get_store)):
    """追加消息到对话"""
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
def rename_conversation(conv_id: str, data: RenameRequest, store=Depends(get_store)):
    # ⚠️ 此前「没找到」也返回 **HTTP 200 + {"status": "not found"}** —— 前端 `apiFetch`
    # 只看 `r.ok`，会把重命名失败当成成功（乐观更新从此永不回滚）。存在性失败必须走
    # 404，与同文件 get/patch/append/rebuild 四处已有写法一致（模式 #36）。
    if not store.rename_conversation(conv_id, data.title):
        raise AppError("CONV_NOT_FOUND", "对话不存在", status_code=404)
    return {"status": "ok"}


@router.patch("/conversations/{conv_id}")
def patch_conversation(conv_id: str, data: PatchRequest, store=Depends(get_store)):
    """归档/取消归档 — archived 字段"""
    conv = store.get_conversation(conv_id)
    if not conv:
        raise AppError("CONV_NOT_FOUND", "对话不存在", status_code=404)
    if data.archived is not None:
        conv["archived"] = data.archived
    store.save_conversation(conv)
    return {"status": "ok"}


@router.post("/conversations/{conv_id}/clear-context")
def clear_context(conv_id: str, data: ClearContextRequest, store=Depends(get_store)):
    """清除上下文边界 — 不删消息，停止携带该索引前历史（对齐 NextChat clearContextIndex）"""
    # ⚠️ 同上：`{"status": "not found"}` + 200 会让前端把「对话没了」读成「已清除」。
    if not SessionManager(store).set_clear_context(conv_id, data.index):
        raise AppError("CONV_NOT_FOUND", "对话不存在", status_code=404)
    return {"status": "ok"}


@router.put("/conversations/{conv_id}/messages")
def rebuild_messages(conv_id: str, data: RebuildRequest, store=Depends(get_store)):
    """全量重建消息列表 — 前端编辑/删除/分支截断统一走此接口，避免逐条改动的上下文错乱"""
    conv = store.get_conversation(conv_id)
    if not conv:
        raise AppError("CONV_NOT_FOUND", "对话不存在", status_code=404)
    conv["messages"] = data.messages
    conv["updated_at"] = datetime.now().isoformat()
    store.save_conversation(conv)
    return {"status": "ok", "count": len(data.messages)}


@router.delete("/conversations/{conv_id}")
def delete_conversation(conv_id: str, store=Depends(get_store)):
    # ⚠️ 此前**不查存在性**，无条件回 `{"status": "deleted"}` —— `store.delete_conversation`
    # 恒返回 True（本就无存在性判定），故「删除一个已经没了的对话」在 API 层与真删同形。
    if not store.get_conversation(conv_id):
        raise AppError("CONV_NOT_FOUND", "对话不存在", status_code=404)
    store.delete_conversation(conv_id)
    return {"status": "deleted"}
