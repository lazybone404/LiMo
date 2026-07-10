"""
导入路由 — POST /api/ingest + SSE streaming
"""

import asyncio
import hashlib
import json
import logging
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel

from app.core.types import PipelineInput, InputType, RawTranscript, ProgressEvent
from app.core.registry import AdapterRegistry
from app.core.errors import AppError
from app.core.config import build_engine_models

logger = logging.getLogger(__name__)
router = APIRouter()

# 上传文件最大 2GB（适应大视频文件）
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
# 超过此阈值改为流式写入磁盘，避免 OOM
STREAM_THRESHOLD = 100 * 1024 * 1024  # 100MB
# 流式写入每次读取的块大小
STREAM_CHUNK_SIZE = 8 * 1024 * 1024  # 8MB

def _safe_filename(filename: str) -> str:
    """从用户提供的文件名中提取安全的文件名部分，防路径遍历"""
    if not filename:
        return "unknown"
    # 只取最后一段，去掉路径分隔符
    safe = Path(filename).name
    # 去掉前导点和空字符
    safe = safe.lstrip(".").replace("\0", "")
    return safe or "unknown"


class UpdateTranscriptRequest(BaseModel):
    title: Optional[str] = None
    tags: Optional[list[str]] = None

@router.get("/transcripts")
async def list_transcripts(request: Request, q: str = "", sourceType: str = "", tag: str = "") -> list[dict[str, Any]]:
    store = request.app.state.store
    filters = {}
    if q:
        filters["query"] = q
    if sourceType:
        filters["sourceType"] = sourceType
    if tag:
        filters["tag"] = tag
    return store.list_transcripts(filters if filters else None)


@router.get("/transcripts/{tx_id}")
async def get_transcript(tx_id: str, request: Request) -> dict[str, Any]:
    """获取单个 RawTranscript 完整内容（含 rawText）"""
    store = request.app.state.store
    tx = store.get_transcript(tx_id)
    if not tx:
        raise HTTPException(status_code=404, detail="文档不存在")
    # 不返回 rawText 全文（太大），仅返回元数据+前2000字符预览
    return {
        "id": tx["id"],
        "title": tx.get("title", ""),
        "sourceType": tx.get("sourceType", ""),
        "sourceUrl": tx.get("sourceUrl", ""),
        "engine": tx.get("engine", ""),
        "language": tx.get("metadata", {}).get("language", ""),
        "duration": tx.get("metadata", {}).get("duration"),
        "coverUrl": tx.get("metadata", {}).get("cover_url", ""),
        "contentHash": tx.get("contentHash", ""),
        "tags": tx.get("tags", []),
        "createdAt": tx.get("createdAt", ""),
        "rawText": tx.get("rawText", ""),
    }


@router.patch("/transcripts/{tx_id}")
async def update_transcript(tx_id: str, request: Request) -> dict[str, Any]:
    """更新文档标题/标签"""
    store = request.app.state.store
    tx = store.get_transcript(tx_id)
    if not tx:
        raise HTTPException(status_code=404, detail="文档不存在")

    body = json.loads(await request.body())
    title = body.get("title")
    tags = body.get("tags")

    # 更新字段
    if title is not None:
        tx["title"] = title
    if tags is not None:
        tx["tags"] = tags

    # 写回磁盘 + 更新索引
    store.save_transcript_full(tx)

    return {"status": "ok", "title": tx.get("title", ""), "tags": tx.get("tags", [])}


@router.delete("/transcripts/{tx_id}")
async def delete_transcript(tx_id: str, request: Request) -> dict[str, Any]:
    """删除文档（同时删除关联的 Markdown）"""
    store = request.app.state.store
    tx = store.get_transcript(tx_id)
    if not tx:
        raise HTTPException(status_code=404, detail="文档不存在")

    store.delete_transcript(tx_id)
    return {"status": "deleted", "id": tx_id}


_ADAPTER_SOURCE_MAP = {
    "video": "video", "webpage": "webpage",
    "image": "image", "text": "text",
}

# ── 共享辅助函数 ──

def _build_engine_config(config: dict, media_dir: str) -> dict:
    """构建传给 adapter.adapt() 的引擎配置（ingest/retranscribe/stream 共用）"""
    return {
        "engine_bindings": config.get("engine_bindings", {}),
        "engine_models": build_engine_models(config),
        "engine_providers": config.get("engine_providers", []),
        "engine_auto_fallback": config.get("engine_auto_fallback", True),
        "media_dir": media_dir,
    }


def _inject_cookies(adapter: object, inp: PipelineInput, config: dict) -> None:
    """为视频适配器注入平台 Cookie + 代理"""
    if adapter.name != "video" or not hasattr(adapter, 'cookies'):
        return
    auth = config.get("auth", {})
    url_domain = (inp.url or "").lower()
    bili_cookie = auth.get("bilibili", "")
    youtube_cookie = auth.get("youtube", "")
    douyin_cookie = auth.get("douyin", "")
    if "bilibili" in url_domain and bili_cookie:
        adapter.cookies = bili_cookie.strip().strip("'\"").strip()
        logger.info("注入 B站 Cookie")
    elif ("youtube" in url_domain or "youtu.be" in url_domain) and youtube_cookie:
        adapter.cookies = youtube_cookie
        logger.info("注入 YouTube Cookie")
    elif "douyin" in url_domain and douyin_cookie:
        # 兼容多种格式：直接粘贴的 key=value、Chrome 表格、Netscape
        from app.core.cookie_parser import auto_parse
        parsed = auto_parse(douyin_cookie)
        if parsed.get("cookies"):
            # 转为 HTTP Cookie header 格式: key=value; key=value
            adapter.cookies = "; ".join(
                f"{k}={v}" for k, v in parsed["cookies"].items()
            )
        else:
            adapter.cookies = douyin_cookie.strip().strip("'\"").strip()
        logger.info("注入抖音 Cookie (%d 条)", len(parsed.get("cookies", {})))
    # 注入代理设置（用于 yt-dlp 下载 + oEmbed API）
    proxy = config.get("fetch", {}).get("proxy", "")
    if proxy and hasattr(adapter, 'cookies'):
        adapter.proxy = proxy.strip()


def _build_title(inp: PipelineInput) -> str:
    """从 PipelineInput 提取文档标题"""
    if inp.file_path:
        stem = Path(inp.file_path).stem
        # 技术名 → 自然语言：DSpark_paper → DSpark paper
        stem = re.sub(r'[_-]', ' ', stem).strip()
        stem = re.sub(r'\s{2,}', ' ', stem)
        return stem[:80] if len(stem) > 80 else stem
    if inp.url:
        from urllib.parse import urlparse
        p = urlparse(inp.url)
        # 域名 + 路径末段
        parts = [s for s in p.path.strip("/").split("/") if s]
        last = parts[-1] if parts else ""
        # 去掉已知扩展名（仅文件后缀，不误伤 arxiv ID 等含点的路径段）
        _KNOWN_EXT = {".html", ".htm", ".pdf", ".docx", ".pptx", ".xlsx",
                      ".epub", ".png", ".jpg", ".jpeg", ".gif", ".webp",
                      ".mp4", ".mkv", ".avi", ".mov", ".mp3", ".wav"}
        if any(last.lower().endswith(ext) for ext in _KNOWN_EXT):
            last = last.rsplit(".", 1)[0]
        last = re.sub(r'[_-]', ' ', last).strip()
        if last and len(last) >= 2:
            return last[:80]
        # 无有意义路径 → 用域名
        return p.netloc[:80]
    return (inp.text or "")[:40] + ("..." if len(inp.text or "") > 40 else "")


def _extract_title_from_rawtext(raw_text: str) -> str | None:
    """从 rawText 内容提取标题。Markdown 首行 # / ## 标题，≤20 字返回。"""
    if not raw_text:
        return None
    m = re.match(r'^#{1,2}\s+(.+?)(?:\s*$|\n)', raw_text)
    if m:
        t = m.group(1).strip()
        if 2 <= len(t) <= 60:
            return t
    return None


async def _persist_ingest_result(
    store, output, adapter_name: str,
    inp: PipelineInput, media_dir: str,
) -> dict:
    """持久化适配器输出 → 内容去重 → 保存 RawTranscript → 返回结果 dict。

     ingest() 和 ingest_stream() 共用此函数。
    """
    output.raw_text = _persist_media_file(store, output.raw_text)

    content_hash = hashlib.sha256(output.raw_text.encode()).hexdigest()
    existing_hash = store.find_by_hash(content_hash)
    if existing_hash:
        return {
            "status": "duplicate_content",
            "id": existing_hash["id"],
            "title": existing_hash.get("title", ""),
            "message": f"内容与已有《{existing_hash.get('title', '')}》完全相同",
        }

    tx_id = f"tr_{uuid.uuid4().hex[:12]}"
    output.raw_text = _persist_media_images(store, output.raw_text, media_dir, tx_id)
    _persist_original_image(store, output.raw_text, tx_id)

    # 图片类型：原图就是封面
    if adapter_name == "image" and output.metadata and not output.metadata.cover_url:
        output.metadata.cover_url = f"/api/files/{tx_id}/original.png"

    # 视频类型：封面缩略图下载到本地（几十KB，去外部CDN依赖）
    if output.metadata and output.metadata.cover_url:
        output.metadata.cover_url = _persist_cover_image(
            store, output.metadata.cover_url, tx_id)

    title = (output.metadata.source_title if output.metadata and output.metadata.source_title
             else _build_title(inp))
    # 如果标题看起来像自动生成的（含协议/哈希/全是技术名），从内容提取
    # URL 输入的标题始终尝试从正文提取（正文首行标题通常比 URL 路径段更可读）
    if (not output.metadata or not output.metadata.source_title) and (
        title.startswith("http") or re.match(r'^[a-f0-9]{32}', title) or
        "_" in title or len(title) > 40 or bool(inp.url)
    ):
        extracted = _extract_title_from_rawtext(output.raw_text)
        if extracted:
            title = extracted
    tx = RawTranscript(
        id=tx_id, title=title,
        source_type=_adapter_to_source_type(adapter_name, inp.file_path or ""),
        engine=output.engine, raw_text=output.raw_text,
        content_hash=content_hash, metadata=output.metadata,
        source_url=inp.url,
        source_file_name=Path(inp.file_path).name if inp.file_path else None,
        source_file_path=inp.file_path,
    )

    if not store.save_transcript(tx):
        existing = store.find_by_hash(content_hash)
        if existing:
            return {
                "status": "duplicate_content",
                "id": existing["id"],
                "title": existing.get("title", ""),
                "message": f"内容与已有《{existing.get('title', '')}》完全相同",
            }

    return {
        "status": "done",
        "id": tx.id, "title": tx.title,
        "sourceType": tx.source_type.value if hasattr(tx.source_type, 'value') else tx.source_type,
        "sourceFileName": tx.source_file_name,
        "engine": tx.engine, "count": store.count,
    }

def _adapter_to_source_type(name: str, file_path: str = "") -> str:
    """根据适配器名称 + 文件扩展名确定 sourceType。

    document 适配器覆盖 5 种文件类型，根据扩展名细分为独立 sourceType，
    确保前端能正确区分 PDF 专属选项（翻译/公式/参考文献）和通用文档选项。
    """
    if name == "document":
        ext = Path(file_path).suffix.lower() if file_path else ""
        if ext == ".pdf":
            return "pdf"
        if ext in (".docx", ".doc"):
            return "docx"
        if ext in (".pptx", ".ppt"):
            return "pptx"
        if ext in (".xlsx", ".xls"):
            return "xlsx"
        if ext == ".epub":
            return "epub"
        # 无扩展名或未知 → pdf 兜底
        return "pdf"
    return _ADAPTER_SOURCE_MAP.get(name, "text")


def _persist_media_file(store: object, raw_text: str) -> str:
    """将 raw_text 中引用的临时文件复制到永久目录，返回更新后的 raw_text"""
    try:
        meta = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        return raw_text

    file_path = meta.get("file_path", "")
    if not file_path:
        return raw_text

    src = Path(file_path)
    if not src.exists():
        logger.warning("持久化跳过: 临时文件已不存在 %s", src)
        return raw_text

    # 目标：store.root/files/{hash}/{filename}
    file_hash = hashlib.sha256(str(src).encode()).hexdigest()[:12]
    dest_dir = Path(store.root) / "files" / file_hash
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name

    # 避免重复复制
    if dest.exists() and dest.stat().st_size == src.stat().st_size:
        logger.info("持久化跳过: 文件已存在 %s", dest)
    else:
        shutil.copy2(src, dest)
        logger.info("持久化: %s → %s (%.1fKB)", src.name, dest, dest.stat().st_size / 1024)

    # 更新 JSON 中的路径
    meta["file_path"] = str(dest)
    return json.dumps(meta, ensure_ascii=False)


def _persist_media_images(store: object, raw_text: str, media_dir: str, tx_id: str) -> str:
    """将引擎提取的图片从临时目录迁移到永久目录，改写 rawText 中路径。

    引擎写入 `![alt](img_0001.png)` → 改写为 `![alt](/api/files/{tx_id}/img_0001.png)`
    无图片时直接返回原文。
    """
    media = Path(media_dir)
    if not media.exists() or not any(media.iterdir()):
        return raw_text  # 无图片，无需处理

    dest_dir = Path(store.root) / "files" / tx_id
    dest_dir.mkdir(parents=True, exist_ok=True)

    import re
    count = 0
    all_imgs = set(media.glob("img_*")) | set(media.glob("*.png")) | set(media.glob("*.jpg")) | set(media.glob("*.jpeg"))
    for img_file in sorted(all_imgs):
        # 统一重命名为 img_NNNN 格式
        new_name = f"img_{count:04d}{img_file.suffix}"
        dest = dest_dir / new_name
        shutil.copy2(img_file, dest)
        # 替换 Markdown：匹配 ![](任意路径/文件名) → ![](/api/files/{tx_id}/new_name)
        escaped = re.escape(img_file.name)       # 匹配纯文件名
        escaped_full = re.escape(str(img_file))  # 匹配绝对/相对路径
        raw_text = re.sub(
            rf'!\[([^\]]*)\]\s*\([^)]*{escaped}\)',
            rf'![\1](/api/files/{tx_id}/{new_name})',
            raw_text,
        )
        count += 1

    # 清理临时目录
    try:
        shutil.rmtree(media)
    except OSError:
        pass

    if count:
        logger.info("图片持久化: %d 个 → %s", count, dest_dir)
    return raw_text


def _persist_cover_image(store: object, cover_url: str, tx_id: str) -> str:
    """下载视频封面缩略图到本地存储，返回本地 URL。

    视频封面通常几十KB，存本地消除外部 CDN 依赖。
    已有的本地路径（如 /api/files/...）不做处理直接返回。
    """
    if not cover_url or cover_url.startswith("/api/files/"):
        return cover_url  # 已是本地路径或为空
    from app.utils.url_security import validate_url
    try:
        validate_url(cover_url)
    except ValueError as e:
        logger.warning("封面 URL 安全校验失败: %s — %s", cover_url[:80], str(e)[:80])
        return cover_url
    try:
        dest_dir = Path(store.root) / "files" / tx_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        ext = ".jpg"
        url_lower = cover_url.lower()
        if ".png" in url_lower:
            ext = ".png"
        elif ".webp" in url_lower:
            ext = ".webp"
        dest = dest_dir / f"cover{ext}"
        if dest.exists():
            return f"/api/files/{tx_id}/cover"
        import requests as req
        resp = req.get(cover_url, headers={"User-Agent": "LiMo/1.0"}, timeout=15)
        if resp.status_code == 200:
            dest.write_bytes(resp.content)
            logger.info("封面下载: %s → %s (%d bytes)", cover_url[:80], dest.name, len(resp.content))
            return f"/api/files/{tx_id}/cover"
        else:
            logger.warning("封面下载失败 HTTP %d: %s", resp.status_code, cover_url[:80])
            return cover_url  # 回退，保留外部 URL
    except Exception as e:
        logger.warning("封面下载异常: %s — %s", cover_url[:80], str(e)[:80])
        return cover_url  # 下载失败不影响导入流程，保留外部 URL


def _persist_original_image(store: object, raw_text: str, tx_id: str) -> None:
    """图片导入：将原图从持久化位置拷贝到 {tx_id}/original.ext，前端原图 tab 直达。

    ImageAdapter 输出 JSON {"type":"image","file_path":"/store/files/hash/img.png"}，
    _persist_media_file 已将文件拷到永久目录。此处再拷一份到可预测的 URL 路径。
    """
    try:
        meta = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        return
    if meta.get("type") != "image":
        return

    file_path = meta.get("file_path", "")
    src = Path(file_path)
    if not src.exists():
        logger.warning("原图拷贝跳过: 源文件不存在 %s", src)
        return

    dest_dir = Path(store.root) / "files" / tx_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "original.png"
    if not dest.exists():
        shutil.copy2(src, dest)
        logger.info("原图拷贝: %s → %s", src.name, dest)


class _FileTooLarge(Exception):
    """文件过大异常，由调用方转为用户友好的错误响应"""

    def __init__(self, max_bytes: int):
        self.max_bytes = max_bytes


async def _handle_file_upload(file: UploadFile, content_length: str | None, store_root: Path) -> Path:
    """处理文件上传：校验大小 + 流式/小文件写盘，返回临时文件路径。

    大文件（>100MB）流式写入，小文件内存读取后写盘。
    超出 MAX_UPLOAD_BYTES 抛出 _FileTooLarge。
    """
    cl = int(content_length) if content_length else 0
    if cl > MAX_UPLOAD_BYTES:
        raise _FileTooLarge(MAX_UPLOAD_BYTES)
    tmp_dir = Path(store_root) / "tmp"
    tmp_dir.mkdir(exist_ok=True)
    safe_name = _safe_filename(file.filename or "")
    tmp_path = tmp_dir / f"{uuid.uuid4().hex}_{safe_name}"
    if cl > STREAM_THRESHOLD:
        total = 0
        with open(tmp_path, "wb") as f:
            while True:
                chunk = await file.read(STREAM_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    try: tmp_path.unlink()
                    except OSError: pass
                    raise _FileTooLarge(MAX_UPLOAD_BYTES)
                f.write(chunk)
        logger.info("流式写入完成: %s (%.1f MB)", safe_name, total / (1024 * 1024))
    else:
        data = await file.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise _FileTooLarge(MAX_UPLOAD_BYTES)
        tmp_path.write_bytes(data)
    return tmp_path


@router.post("/ingest")
async def ingest(
    request: Request,
    url: Optional[str] = Form(None),
    text: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    force: bool = Form(False),
):
    store = request.app.state.store

    # 构造 PipelineInput
    if file:
        # 检查文件大小（Content-Length header）
        content_length = request.headers.get("content-length")
        cl = int(content_length) if content_length else 0
        if cl > MAX_UPLOAD_BYTES:
            raise AppError("INGEST_FILE_TOO_LARGE", f"文件过大（最大 {MAX_UPLOAD_BYTES // (1024*1024)}MB）", recoverable=False)
        tmp_dir = Path(store.root) / "tmp"
        tmp_dir.mkdir(exist_ok=True)
        safe_name = _safe_filename(file.filename or "")
        tmp_path = tmp_dir / f"{uuid.uuid4().hex}_{safe_name}"
        # 大文件（>100MB）流式写入磁盘，小文件保持内存读取
        if cl > STREAM_THRESHOLD:
            total = 0
            with open(tmp_path, "wb") as f:
                while True:
                    chunk = await file.read(STREAM_CHUNK_SIZE)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        try: tmp_path.unlink()
                        except OSError: pass
                        raise AppError("INGEST_FILE_TOO_LARGE", f"文件过大（最大 {MAX_UPLOAD_BYTES // (1024*1024)}MB）", recoverable=False)
                    f.write(chunk)
            logger.info("流式写入完成: %s (%.1f MB)", safe_name, total / (1024*1024))
        else:
            data = await file.read()
            if len(data) > MAX_UPLOAD_BYTES:
                raise AppError("INGEST_FILE_TOO_LARGE", f"文件过大（最大 {MAX_UPLOAD_BYTES // (1024*1024)}MB）", recoverable=False)
            tmp_path.write_bytes(data)
        inp = PipelineInput(type=InputType.FILE, file_path=str(tmp_path))
    elif url:
        inp = PipelineInput(type=InputType.URL, url=url)
    elif text:
        inp = PipelineInput(type=InputType.TEXT, text=text)
    else:
        raise AppError("INGEST_NO_INPUT", "请提供 url / text / file 之一", recoverable=False)

    # 路由适配器
    adapter = AdapterRegistry.route(inp)
    if not adapter:
        raise AppError("INGEST_UNSUPPORTED", "不支持的输入格式",
                       detail=", ".join(a.name for a in AdapterRegistry.all()), recoverable=False)

    # 注入平台 Cookie + URL 去重
    config = request.app.state.config
    _inject_cookies(adapter, inp, config)

    # URL 去重（force=true 时跳过）
    if inp.url and not force:
        existing_url = store.find_by_url(inp.url)
        if existing_url:
            return {
                "id": existing_url["id"],
                "title": existing_url.get("title", ""),
                "status": "duplicate_url",
                "message": f"已导入过《{existing_url.get('title', '')}》",
                "count": store.count,
            }

    # 为转录图片创建临时目录（引擎写入文件而非 base64）
    import tempfile
    media_dir = tempfile.mkdtemp(prefix="mbw_img_")

    try:
        output = await adapter.adapt(inp, engine_config=_build_engine_config(config, media_dir))
    except AppError:
        raise
    except Exception as e:
        logger.exception("Ingest failed")
        raise AppError("INGEST_FAILED", f"导入失败: {str(e)[:200]}") from e

    return await _persist_ingest_result(store, output, adapter.name, inp, media_dir)



@router.post("/transcripts/{tx_id}/retranscribe")
async def retranscribe(tx_id: str, request: Request) -> dict[str, Any]:
    """重新转录 — 用当前引擎绑定重新处理原始来源

    - URL 导入：重新下载 + 转录
    - 本地文件：直接引用存储的路径（不复制），重新转录
    """
    guard = request.app.state.guard
    # 重转录用本地信号量（不抢占生成/处理的云端槽位）
    async with guard.local_semaphore:
        store = request.app.state.store
        config = request.app.state.config
        tx = store.get_transcript(tx_id)
        if not tx:
            raise AppError("RETX_NOT_FOUND", "文档不存在", recoverable=False)

        source_url = tx.get("sourceUrl", "")
        source_file_path = tx.get("sourceFilePath", "")

        if source_file_path and Path(source_file_path).exists():
            inp = PipelineInput(type=InputType.FILE, file_path=source_file_path)
        elif source_url:
            inp = PipelineInput(type=InputType.URL, url=source_url)
        else:
            raise AppError("RETX_NO_SOURCE", "该文档没有可用的原始来源（URL 或本地文件路径）", recoverable=False)

        adapter = AdapterRegistry.route(inp)
        if not adapter:
            raise AppError("RETX_UNSUPPORTED", "不支持的输入格式", recoverable=False)

        # 注入平台 Cookie
        _inject_cookies(adapter, inp, config)

        # 重转录 — 创建临时图片目录
        import tempfile
        media_dir = tempfile.mkdtemp(prefix="mbw_img_")

        # 后台监听客户端断连 → 取消长时间操作
        cancel_event = asyncio.Event()
        async def _watch_disconnect():
            while not cancel_event.is_set():
                if await request.is_disconnected():
                    cancel_event.set()
                    return
                await asyncio.sleep(0.5)
        disconnect_task = asyncio.create_task(_watch_disconnect())

        try:
            engine_cfg = _build_engine_config(config, media_dir)
            engine_cfg["cancel_event"] = cancel_event
            output = await adapter.adapt(inp, engine_config=engine_cfg)
        except asyncio.CancelledError:
            raise AppError("RETX_CANCELLED", "操作已取消", recoverable=True)
        except AppError:
            raise
        except Exception as e:
            logger.exception("Re-transcribe failed")
            raise AppError("RETX_FAILED", f"重新转录失败: {str(e)[:200]}") from e
        finally:
            cancel_event.set()
            disconnect_task.cancel()
            try: await disconnect_task
            except asyncio.CancelledError: pass

        # 持久化引擎提取的图片，重写 rawText 中的路径
        output.raw_text = _persist_media_images(store, output.raw_text, media_dir, tx_id)
        _persist_original_image(store, output.raw_text, tx_id)

        # 图片类型：原图就是封面（retranscribe 补上）
        if not tx.get("metadata", {}).get("cover_url") and adapter.name == "image":
            tx.setdefault("metadata", {})["cover_url"] = f"/api/files/{tx_id}/original.png"

        # 视频类型：封面下载到本地（新数据或旧数据首次转本地）
        cover_url = tx.get("metadata", {}).get("cover_url", "")
        if cover_url and not cover_url.startswith("/api/files/"):
            cover_url = _persist_cover_image(store, cover_url, tx_id)
            tx.setdefault("metadata", {})["cover_url"] = cover_url

        # 覆盖 rawText（保留已有 markdown 生成结果）
        if not output.raw_text or not output.raw_text.strip():
            raise AppError(
                "RETX_EMPTY",
                f"重新转录失败: {output.engine or '所有引擎'} 返回空结果。旧内容已保留。",
                recoverable=True,
            )
        tx["rawText"] = output.raw_text
        tx["engine"] = output.engine
        tx["contentHash"] = hashlib.sha256(output.raw_text.encode()).hexdigest()
        metadata = tx.get("metadata", {})
        if output.metadata.pages:
            metadata["pages"] = output.metadata.pages
        if output.metadata.language:
            metadata["language"] = output.metadata.language
        if output.metadata.duration:
            metadata["duration"] = output.metadata.duration
        tx["metadata"] = metadata

        # 写回（同 id 覆盖文件，更新索引中的条目）
        store.save_transcript_full(tx)

        logger.info("重新转录完成: %s engine=%s (%d chars)",
                    tx.get("title", tx_id)[:40], output.engine, len(output.raw_text))
        return {
            "status": "ok",
            "id": tx_id,
            "engine": output.engine,
        }


@router.post("/ingest/stream")
async def ingest_stream(
    request: Request,
    url: Optional[str] = Form(None),
    text: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    force: bool = Form(False),
):
    """SSE 流式导入 — 实时推送进度"""
    store = request.app.state.store

    if file:
        try:
            tmp_path = await _handle_file_upload(file, request.headers.get("content-length"), store.root)
        except _FileTooLarge:
            raise AppError("INGEST_FILE_TOO_LARGE", f"文件过大（最大 {MAX_UPLOAD_BYTES // (1024*1024)}MB）", recoverable=False) from None
        inp = PipelineInput(type=InputType.FILE, file_path=str(tmp_path))
    elif url:
        inp = PipelineInput(type=InputType.URL, url=url)
    elif text:
        inp = PipelineInput(type=InputType.TEXT, text=text)
    else:
        raise AppError("INGEST_NO_INPUT", "请提供 url / text / file 之一", recoverable=False)

    adapter = AdapterRegistry.route(inp)
    if not adapter:
        raise AppError("INGEST_UNSUPPORTED", "不支持的输入格式",
                       detail=", ".join(a.name for a in AdapterRegistry.all()), recoverable=False)

    # 注入平台 Cookie + URL 去重
    config = request.app.state.config
    _inject_cookies(adapter, inp, config)

    if inp.url and not force:
        existing_url = store.find_by_url(inp.url)
        if existing_url:
            return {
                "id": existing_url["id"],
                "title": existing_url.get("title", ""),
                "status": "duplicate_url",
                "message": f"已导入过《{existing_url.get('title', '')}》",
                "count": store.count,
            }

    import tempfile
    media_dir = tempfile.mkdtemp(prefix="mbw_img_")

    queue: asyncio.Queue = asyncio.Queue()

    def on_progress(ev: ProgressEvent):
        """将进度事件推入队列"""
        try:
            queue.put_nowait({
                "type": "progress",
                "phase": ev.phase,
                "status": ev.status,
                "percent": ev.percent,
                "message": ev.message,
            })
        except Exception:
            logger.warning("Failed to push progress event to SSE queue", exc_info=True)

    async def run_pipeline():
        """在后台运行适配器管道"""
        try:
            output = await adapter.adapt(inp, on_progress=on_progress,
                                          engine_config=_build_engine_config(config, media_dir))
        except AppError as e:
            queue.put_nowait({"type": "error", "message": e.message, "code": e.code})
            return
        except Exception as e:
            logger.exception("Ingest stream failed")
            queue.put_nowait({"type": "error", "message": str(e)})
            return

        result = await _persist_ingest_result(store, output, adapter.name, inp, media_dir)
        queue.put_nowait({"type": "done", **result})

    async def event_generator():
        """SSE 事件生成器 — 心跳保持连接，30s 无事件发心跳，15min 无事件判定挂死"""
        task = asyncio.create_task(run_pipeline())
        loop = asyncio.get_event_loop()
        last_event = loop.time()
        QUIET_TIMEOUT = 900  # 15 分钟静默 → 判定管道挂死
        try:
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=30)
                    yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                    last_event = loop.time()
                    if data["type"] in ("done", "error"):
                        break
                except asyncio.TimeoutError:
                    elapsed = loop.time() - last_event
                    if elapsed > QUIET_TIMEOUT:
                        yield f"data: {json.dumps({'type': 'error', 'message': '处理超时（15分钟无响应）'}, ensure_ascii=False)}\n\n"
                        break
                    # SSE 注释作为心跳，保持连接不被代理/浏览器关闭
                    yield ": heartbeat\n\n"
        finally:
            # 客户端断开 → 取消后台任务，释放信号量
            if not task.done():
                task.cancel()
                logger.info("客户端断开，已取消后台管线任务")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ══════════════════════════════════════════════════════════════════
# 图片文件服务 — 转录引擎提取的图片，按 tx_id 隔离
# ══════════════════════════════════════════════════════════════════

@router.get("/files/{tx_id}/{filename}")
async def serve_media_file(tx_id: str, filename: str, request: Request):
    """访问转录文档的内嵌图片。

    安全约束：
    - tx_id / filename 只允许字母数字 + 下划线 + 点 + 连字符
    - 文件必须在 store.root/files/{tx_id}/ 目录下
    - 只允许常见图片格式
    """
    import re
    if not re.match(r'^[a-zA-Z0-9_\-]+$', tx_id):
        raise HTTPException(status_code=400, detail="Invalid tx_id")
    if not re.match(r'^[a-zA-Z0-9_\-.]+$', filename):
        raise HTTPException(status_code=400, detail="Invalid filename")

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ("png", "jpg", "jpeg", "gif", "webp", "svg"):
        raise HTTPException(status_code=400, detail=f"Unsupported format: {ext}")

    store = request.app.state.store
    file_path = Path(store.root) / "files" / tx_id / filename

    # 防路径遍历
    try:
        file_path.resolve().relative_to((Path(store.root) / "files").resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Path traversal denied")

    if file_path.exists():
        return FileResponse(str(file_path))

    # 文件不在本地副本目录 — 回退：从 rawText 的 file_path 读取源文件
    tx = store.get_transcript(tx_id)
    if tx:
        try:
            meta = json.loads(tx.get("rawText", "{}"))
        except (json.JSONDecodeError, TypeError):
            meta = {}
        src = meta.get("file_path", "")
        if src:
            src_path = Path(src).resolve()
            data_root = Path(store.root).resolve()
            try:
                src_path.relative_to(data_root)
            except ValueError:
                raise HTTPException(status_code=403, detail="Path traversal denied")
            if src_path.exists():
                return FileResponse(str(src_path))

    raise HTTPException(status_code=404, detail="File not found")
