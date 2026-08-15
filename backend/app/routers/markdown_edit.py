"""
产物编辑路由 — PUT /api/markdown/{transcript_id}

notes/polished 手动编辑保存：覆盖当前版本（两版制自动保留生成版为 prev 可回退），
置 edited 标记（前端重新生成前确认用）。重新生成链路 save_markdown 不传 edited →
新 entry 无该字段 → 标记自动清除。
"""

import logging
import re
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.dependencies import get_store
from app.core.errors import AppError
from app.core.types import MarkdownView, Style, GenerateMode
from app.storage.store import rewrite_asset_urls

logger = logging.getLogger(__name__)
router = APIRouter()

_EDITABLE_MODES = {"notes", "polished"}
# 编辑内容上限（字符数）：普通产物 <100k，内嵌 base64 图 ~2M，5M 足够宽松且防误提交
_MAX_CONTENT_CHARS = 5_000_000


class MarkdownEditRequest(BaseModel):
    mode: str
    content: str


def _unrewrite_asset_urls(content: str, tx_id: str) -> str:
    """GET 返回时 rewrite_asset_urls 把 assets/xxx 改成 /api/assets/{tx_id}/xxx；
    编辑保存前反向转回 assets/xxx，避免绝对 URL 写回磁盘污染导出/二次渲染。

    只处理 markdown 图片引用 `![alt](assets/xxx)` 这一种形态（与 rewrite_asset_urls 对称）——
    用户手动输入的 <img src="/api/assets/..."> 等是新增内容，不反转（按用户输入存盘）。"""
    if not content:
        return content
    pat = re.compile(r"!\[([^\]]*)\]\(/api/assets/" + re.escape(tx_id) + r"/([^\s)]+)\)")
    return pat.sub(r"![\1](assets/\2)", content)


@router.put("/markdown/{transcript_id}")
async def edit_markdown(transcript_id: str, req: MarkdownEditRequest,
                        store=Depends(get_store)) -> dict:
    """保存 notes/polished 的手动编辑：覆盖当前版本 + 置 edited 标记。"""
    if req.mode not in _EDITABLE_MODES:
        raise AppError("MD_EDIT_BAD_MODE", "仅支持编辑笔记（notes）/整理版原文（polished）",
                       recoverable=False)

    if not store.get_transcript(transcript_id):
        raise AppError("MD_EDIT_NOT_FOUND", "文档不存在", recoverable=False)

    if not req.content or not req.content.strip():
        raise AppError("MD_EDIT_EMPTY", "内容不能为空", recoverable=False)

    if len(req.content) > _MAX_CONTENT_CHARS:
        raise AppError("MD_EDIT_TOO_LARGE", "内容过大，请精简后再保存", recoverable=False)

    # 编辑语义是「覆盖已有产物」——transcript 存在但无产物时拒绝（前端无产物不显示编辑入口，
    # 此校验为 API 层防御，防直接调用创建出无源产物）
    mds = store.get_markdowns_by_transcript(transcript_id, mode=req.mode)
    if not mds:
        raise AppError("MD_EDIT_NO_PRODUCT", "该文档还没有可编辑的产物", recoverable=False)

    # 保留原 style（编辑不改样式语义；旧 entry 缺省 minimal）
    style = "minimal"
    try:
        style = Style(mds[0].get("style", "minimal")).value
    except ValueError:
        style = "minimal"

    content = _unrewrite_asset_urls(req.content, transcript_id)
    mv = MarkdownView(
        id=f"md_{uuid.uuid4().hex}", transcript_id=transcript_id,
        content=content, style=style, mode=GenerateMode(req.mode),
    )
    if not store.save_markdown(mv, extra={"edited": True}):
        raise AppError("MD_EDIT_SAVE_FAILED", "保存失败，请重试", recoverable=True)

    # 返回对齐 GET /api/markdown：最新 entry + versions + edited
    mds = store.get_markdowns_by_transcript(transcript_id, mode=req.mode)
    latest = mds[0] if mds else None
    if not latest:
        raise AppError("MD_EDIT_SAVE_FAILED", "保存后未找到产物，请重试", recoverable=True)
    result = {
        **latest,
        "edited": True,
        "versions": [
            {"version": m.get("version", 1), "id": m.get("id"),
             "createdAt": m.get("createdAt"), "status": m.get("status", "complete")}
            for m in mds
        ],
    }
    if result.get("content"):
        result["content"] = rewrite_asset_urls(result["content"], transcript_id)
    return result
