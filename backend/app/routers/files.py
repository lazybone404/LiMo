"""
文件服务 — 封面图片等特殊文件（通用媒体文件由 ingest.py serve_media_file 处理）
"""

import logging
import re
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from app.core.dependencies import get_store

logger = logging.getLogger(__name__)

router = APIRouter(tags=["files"])

MIME_MAP = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".pdf": "application/pdf",
}

_ID_RE = re.compile(r'^[a-zA-Z0-9_\-]+$')

# 封面重定向允许的域名白名单
_COVER_DOMAINS = {"i.ytimg.com", "img.youtube.com", "i0.hdslb.com", "i1.hdslb.com",
                   "i2.hdslb.com", "pic.bilibili.cc", "yt3.ggpht.com"}


def _check_tx_id(transcript_id: str) -> bool:
    return bool(_ID_RE.match(transcript_id)) and len(transcript_id) <= 64


def _safe_local_path(store_root: str, transcript_id: str, filename: str) -> Path | None:
    raw = Path(store_root) / "files" / transcript_id / filename
    resolved = raw.resolve()
    data_root = Path(store_root).resolve()
    try:
        resolved.relative_to(data_root)
    except ValueError:
        logger.warning("路径遍历尝试: %s", raw)
        return None
    return resolved


@router.get("/files/{transcript_id}/cover")
def get_cover_image(transcript_id: str, store=Depends(get_store)):
    """返回文档的本地封面缩略图。

    视频导入时由 _persist_cover_image 下载到
    {data_dir}/files/{tx_id}/cover.{jpg,png,webp}。
    """
    if not _check_tx_id(transcript_id):
        return {"error": "无效的文档 ID"}

    tx = store.get_transcript(transcript_id)
    if not tx:
        return {"error": "文档不存在"}

    for ext in (".jpg", ".png", ".webp"):
        path = _safe_local_path(store.root, transcript_id, f"cover{ext}")
        if path and path.exists():
            media_type = MIME_MAP.get(ext, "image/jpeg")
            return FileResponse(str(path), media_type=media_type, filename=f"cover{ext}")

    # 回退：外部 URL（旧数据没有本地封面）
    cover_url = tx.get("metadata", {}).get("cover_url", "")
    if cover_url and cover_url.startswith(("http://", "https://")):
        from urllib.parse import urlparse
        domain = urlparse(cover_url).hostname or ""
        # 精确匹配白名单域名（杜绝后缀攻击如 evil.hdslb.com.attacker.com）
        is_allowed = (
            domain in _COVER_DOMAINS or
            (domain.endswith(".hdslb.com") and domain.count(".") >= 2 and
             domain.split(".")[-2] == "hdslb")
        )
        if is_allowed:
            from fastapi.responses import RedirectResponse
            return RedirectResponse(url=cover_url)
        logger.warning("封面重定向域名不在白名单: %s", domain)

    return {"error": "没有封面"}
