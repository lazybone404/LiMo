"""
导入路由 — POST /api/ingest + SSE streaming
"""

import asyncio
import hashlib
import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Request, UploadFile, File, Form, Depends
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel

from app.core.types import PipelineInput, InputType, RawTranscript, ProgressEvent
from app.core.registry import AdapterRegistry
from app.core.dependencies import get_store, get_config, get_guard, get_query_service
from app.core.errors import AppError
from app.core.resource_id import is_valid_resource_id
from app.core.config import build_engine_models
from app.core.disconnect import disconnect_watcher
from app.core import logging as log
from app.core.search import update_index
from app.storage.store import rewrite_asset_urls

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
async def list_transcripts(request: Request, svc=Depends(get_query_service), q: str = "", sourceType: str = "", tag: str = "", folderId: Optional[str] = None) -> list[dict[str, Any]]:
    """文档列表。

    ⚠️ **响应仍是裸 list**（不是 `Page`）：本端点的消费者是前端文档库，它自己做了
    分批渲染；改成包装对象会是一次前端联动改造，而分页能力是给 CLI 的**新**契约，
    不该顺手改掉既有端点的形状。`limit=None` 即为此（见 `QueryService.list_resources`）。
    """
    page = svc.list_resources(
        query=q or None, source_type=sourceType or None, tag=tag or None,
        # ⚠️ folderId 的「空串 = 只看未分类」语义由 service 内的 `is not None` 判定
        # 保证——这里是**原样透传**（`None` 与 `""` 必须保持区别，不能写 `folderId or None`）。
        folder_id=folderId,
        limit=None,
    )
    return page.items


@router.get("/transcripts/{tx_id}")
async def get_transcript(tx_id: str, request: Request, svc=Depends(get_query_service)) -> dict[str, Any]:
    """获取单个 RawTranscript 完整内容（含 rawText）"""
    res = svc.get_resource(tx_id)
    # `assets/xxx` → 绝对 API URL 是 **HTTP 层**的职责：CLI 拿绝对 URL 没有意义，
    # 把改写放进 service 等于强迫所有消费方接受 HTTP 的表示法。
    res["rawText"] = rewrite_asset_urls(res["rawText"], tx_id)
    return res


@router.patch("/transcripts/{tx_id}")
async def update_transcript(tx_id: str, request: Request, store=Depends(get_store)) -> dict[str, Any]:
    """更新文档标题/标签"""
    tx = store.get_transcript(tx_id)
    if not tx:
        raise AppError("INGEST_DOC_NOT_FOUND", "文档不存在",
                        status_code=404, recoverable=True)

    body = json.loads(await request.body())
    title = body.get("title")
    tags = body.get("tags")
    folder_id = body.get("folderId")

    # 更新字段
    if title is not None:
        tx["title"] = title
    if tags is not None:
        tx["tags"] = tags
    if folder_id is not None:
        # "" 是有效值（移出到未分类）；非空则目标文件夹必须存在
        if folder_id and not store.folder_exists(folder_id):
            raise AppError("FOLDER_NOT_FOUND", "目标文件夹不存在",
                           status_code=404, recoverable=True)
        tx["folderId"] = folder_id

    # 写回磁盘 + 更新索引
    # ⚠️ save_transcript_full 是**整体替换** index 条目，故 tx 里必须带全字段——
    # 这正是 get_transcript() 要返回 folderId 的原因（否则改标题=移出文件夹）。
    # ⚠️ 返回值必须查（P1-06）：目录不存在时它返回 False 且**什么都没写**，而下面的
    # 响应体回显的是内存里的新值 —— 不查 = 「告诉用户改成功了，磁盘上没变」，刷新即打脸。
    # 走到这里目录通常还在（上面刚 get_transcript 过），但那是**另一次独立读**。
    if not store.save_transcript_full(tx):
        raise AppError("INGEST_DOC_NOT_FOUND", "文档目录不存在（可能已被外部删除），修改未保存",
                       status_code=404, recoverable=True)
    _index_for_search(request, tx_id)

    return {"status": "ok", "title": tx.get("title", ""), "tags": tx.get("tags", []),
            "folderId": tx.get("folderId", "")}


@router.delete("/transcripts/{tx_id}")
async def delete_transcript(tx_id: str, request: Request, store=Depends(get_store)) -> dict[str, Any]:
    """删除文档（同时删除关联的 Markdown）"""
    tx = store.get_transcript(tx_id)
    if not tx:
        raise AppError("INGEST_DOC_NOT_FOUND", "文档不存在",
                        status_code=404, recoverable=True)

    store.delete_transcript(tx_id)
    # 从全文索引中移除。
    # ⚠️ 这一步**不能静默失败**：store 里文档已经删了、索引里却还留着 → 搜索仍能命中，
    # 点开是 404（「幽灵命中」）。而 `except Exception: pass` 让它**零痕迹**：
    # 用户只看到「删了但还搜得到」，日志里什么都没有。批量路径（见下）就是这么处理的。
    engine = getattr(request.app.state, "search_engine", None)
    if engine is None:
        log.warning("全文索引未就绪，已删除的文档可能仍有搜索残留", tx_id=tx_id)
    else:
        try:
            engine.delete(tx_id)
        except Exception as e:
            log.warning("从全文索引移除失败，搜索可能仍命中该文档",
                        tx_id=tx_id, err=str(e)[:120])
    # 向量索引同理，且**必须在这里做**：`vector_worker.reconcile` 虽然会清孤儿，
    # 但它只在 worker 循环**开始时**跑一次 —— 桌面应用能开着好几天，
    # 这期间孤块既会被语义检索召回到「点进去文档不存在」，也与设置页那句
    # 「删除文档时对应的向量也会一并清掉」不符。
    _drop_vectors(request, tx_id)
    return {"status": "deleted", "id": tx_id}


# ── 批量操作 ──
#
# 相对「前端循环 N 次 PATCH/DELETE」的核心收益：tag/move 在 store 层是**一次加锁
# 一次写盘**（500 个文档 = 1 次 index.json 全量写盘而非 500 次）。

_BATCH_ACTIONS = ("delete", "tag", "move")


class BatchTranscriptsRequest(BaseModel):
    action: str
    ids: list[str] = []
    tags: list[str] = []
    tagMode: str = "add"          # add=并入去重 / replace=整体替换
    # ⚠️ Optional 而非默认 ""：move 时空串是有效值（移出到未分类），
    # 用默认空串就无法区分「没传」与「明确要求移出未分类」。
    folderId: Optional[str] = None


@router.post("/transcripts/batch")
async def batch_transcripts(data: BatchTranscriptsRequest, request: Request,
                            store=Depends(get_store)) -> dict[str, Any]:
    """批量删除 / 打标签 / 移动文件夹。

    返回 {status, affected, failed:[{id,error}]}——**失败必须显式返回明细**
    （删除无法原子回滚，静默吞掉会变成「用户以为全删了」）。
    全部失败 → 400，避免前端把「一条没成」当成功。
    """
    # ① 按 action 做显式枚举校验，不静默返回 affected:0
    if data.action not in _BATCH_ACTIONS:
        raise AppError("BATCH_BAD_ACTION",
                       f"未知批量操作：{data.action}（可选 {', '.join(_BATCH_ACTIONS)}）",
                       status_code=400, recoverable=True)
    if not data.ids:
        raise AppError("BATCH_NO_IDS", "未指定任何文档",
                       status_code=400, recoverable=True)
    if data.action == "tag":
        if not data.tags:
            raise AppError("BATCH_NO_TAGS", "批量打标签必须提供 tags",
                           status_code=400, recoverable=True)
        if data.tagMode not in ("add", "replace"):
            raise AppError("BATCH_BAD_TAG_MODE",
                           f"未知 tagMode：{data.tagMode}（可选 add, replace）",
                           status_code=400, recoverable=True)
    if data.action == "move":
        if data.folderId is None:
            raise AppError("BATCH_NO_FOLDER",
                           "批量移动必须提供 folderId（空串=移出到未分类）",
                           status_code=400, recoverable=True)
        if data.folderId and not store.folder_exists(data.folderId):
            raise AppError("FOLDER_NOT_FOUND", "目标文件夹不存在",
                           status_code=404, recoverable=True)

    affected = 0
    failed: list[dict] = []

    if data.action == "delete":
        engine = getattr(request.app.state, "search_engine", None)
        for tx_id in data.ids:
            try:
                if not store.get_transcript(tx_id):
                    failed.append({"id": tx_id, "error": "文档不存在"})
                    continue
                store.delete_transcript(tx_id)
                # ⚠️ 漏掉这行 = 全文搜索留下幽灵命中（搜得到，点开 404）
                if engine:
                    engine.delete(tx_id)
                # 向量侧同理。它自己吞异常不回滚 —— 文档已经删了，
                # 让这条记成 failed 只会诱导用户重试，而重试只会得到「文档不存在」。
                _drop_vectors(request, tx_id)
                affected += 1
            except Exception as e:      # 单条失败不拖垮整批
                failed.append({"id": tx_id, "error": str(e)[:120]})
                log.warning("批量删除单条失败", tx_id=tx_id, err=str(e)[:120])
    else:
        # tag / move 是一次加锁一次写盘；不存在的 id 即「未命中」，报为失败并说明
        present: list[str] = []
        for tx_id in data.ids:
            if store.get_transcript(tx_id):
                present.append(tx_id)
            else:
                failed.append({"id": tx_id, "error": "文档不存在"})
        if data.action == "tag":
            affected = store.update_transcripts_tags(present, data.tags, data.tagMode)
        else:
            affected = store.set_transcripts_folder(present, data.folderId)

    if affected == 0 and failed:
        raise AppError("BATCH_ALL_FAILED", f"全部 {len(failed)} 条操作失败",
                       status_code=400, recoverable=True)

    log.info("批量操作", action=data.action, affected=affected, failed=len(failed))
    return {"status": "ok", "affected": affected, "failed": failed}


def _index_for_search(request: Request, tx_id: str):
    """把一篇文档写进全文索引，并给向量侧打 `pending`（薄委托：只负责取依赖）。

    内容判据在 `app.core.search.update_index` → `doc_text.search_index_text`，
    五个写点共用那一份。向量侧只打标记、不在这里编码（见 `core/vector_store`）。
    """
    update_index(getattr(request.app.state, 'search_engine', None),
                 getattr(request.app.state, 'store', None), tx_id,
                 vec=getattr(request.app.state, 'vector_store', None))


def _drop_vectors(request: Request, tx_id: str) -> None:
    """把一篇文档从向量索引里摘掉（薄委托：只负责取依赖 + 不冒 500）。

    与上一条**成对**：写入侧打 `pending`、删除侧摘块，两条路径都不能漏。
    ⚠️ 吞异常是刻意的 —— 走到这里文档已经从 store 里删了、回滚不了，
    抛出去只是把「文档删掉了但向量没清干净」变成一个用户看不懂的 500。
    残留由 `vector_worker.reconcile` 在下次启动对账时兜底。
    """
    vec = getattr(request.app.state, 'vector_store', None)
    if vec is None:
        return
    try:
        vec.delete_doc(tx_id)
    except Exception as e:
        log.warning("从向量索引移除失败，语义检索可能仍命中该文档",
                    tx_id=tx_id, err=str(e)[:120])


_ADAPTER_SOURCE_MAP = {
    "video": "video", "webpage": "webpage",
    "image": "image", "text": "text",
    # arXiv 论文走 PDF 引擎、产物是 PDF 文本 ⇒ 用既有的 `pdf` 而非新增 SourceType
    # （新 SourceType 会强制同步前端 SOURCE_FILTERS / TYPE_COLORS / tabRegistry，
    #  漏一处就是预览区白屏）
    "arxiv": "pdf",
}

# ── 共享辅助函数 ──

def _build_engine_config(config: dict, media_dir: str) -> dict:
    """构建传给 adapter.adapt() 的引擎配置（ingest/retranscribe/stream 共用）"""
    return {
        "engine_bindings": config.get("engine_bindings", {}),
        "engine_models": build_engine_models(config),
        "engine_providers": config.get("engine_providers", []),
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
        log.info("注入 B站 Cookie", domain="bilibili")
    elif ("youtube" in url_domain or "youtu.be" in url_domain) and youtube_cookie:
        adapter.cookies = youtube_cookie
        log.info("注入 YouTube Cookie", domain="youtube")
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
        log.info("注入抖音 Cookie", count=len(parsed.get("cookies", {})))
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

    # 提前计算标题（图片持久化需要 doc_dir）
    title = (output.metadata.source_title if output.metadata and output.metadata.source_title
             else _build_title(inp))
    if (not output.metadata or not output.metadata.source_title) and (
        title.startswith("http") or re.match(r'^[a-f0-9]{32}', title) or
        "_" in title or len(title) > 40 or bool(inp.url)
    ):
        extracted = _extract_title_from_rawtext(output.raw_text)
        if extracted:
            title = extracted

    # 创建文档目录（在图片持久化之前）
    doc_dir = store._get_or_create_doc_dir(tx_id, title)

    output.raw_text = _persist_media_images(output.raw_text, media_dir, doc_dir)
    _persist_original_image(output.raw_text, doc_dir)

    # 图片类型：原图就是封面
    if adapter_name == "image" and output.metadata and not output.metadata.cover_url:
        output.metadata.cover_url = f"/api/assets/{tx_id}/original.png"

    # 视频类型：封面缩略图下载到 doc_dir/assets/（to_thread 释放事件循环）
    if output.metadata and output.metadata.cover_url:
        output.metadata.cover_url = await asyncio.to_thread(
            _persist_cover_image, output.metadata.cover_url, doc_dir, tx_id)

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
        "sourceUrl": tx.source_url,
        "coverUrl": tx.metadata.cover_url if tx.metadata else "",
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
        log.warning("持久化跳过: 临时文件已不存在", src=str(src))
        return raw_text

    # 目标：store.root/files/{hash}/{filename}
    file_hash = hashlib.sha256(str(src).encode()).hexdigest()[:12]
    dest_dir = Path(store.root) / "files" / file_hash
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name

    # 避免重复复制
    if dest.exists() and dest.stat().st_size == src.stat().st_size:
        log.info("持久化跳过: 文件已存在", dest=str(dest))
    else:
        shutil.copy2(src, dest)
        log.info("持久化", src=src.name, dest=str(dest), size_kb=round(dest.stat().st_size / 1024, 1))

    # 复制完成后删除临时源文件（上传到 store.root/tmp 的副本，防累积）
    try:
        src.unlink()
    except OSError:
        pass

    # 更新 JSON 中的路径
    meta["file_path"] = str(dest)
    return json.dumps(meta, ensure_ascii=False)


def _persist_media_images(raw_text: str, media_dir: str, doc_dir: Path) -> str:
    """将引擎提取的图片从临时目录迁移到文档目录，改写 rawText 中路径。

    引擎写入 `![alt](img_0001.png)` → 改写为 `![alt](assets/img_{sha1}.png)`（相对路径）
    无图片时直接返回原文。

    ⚠️ **文件名取内容哈希，不取顺序编号**（2026-10-10 改）。顺序编号（`img_0000`）
    的失效场景是**重新转录**（`ingest.py::retranscribe` 复用同一 `doc_dir` 原地覆盖）：
    旧版本的笔记引用着 `assets/img_0000.png`，而那个文件已被换成新一次转录的第一张图。
    在两版制下这个错误「最多错一版」，而版本**全部保留**之后，它变成「所有历史版本
    的配图都是最新一次的」——同一个 bug，伤害面随改动一起放大了。
    内容寻址后：同图同名（幂等、不累积孤儿文件），不同图不同名（互不覆盖）。
    """
    media = Path(media_dir)
    if not media.exists() or not any(media.iterdir()):
        # 无图片 → 清理空临时目录（此前直接 return，每次导入残留一个 mbw_img_* 空目录）
        try:
            shutil.rmtree(media, ignore_errors=True)
        except OSError:
            pass
        return raw_text

    dest_dir = doc_dir / "assets"
    dest_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    all_imgs = set(media.glob("img_*")) | set(media.glob("*.png")) | set(media.glob("*.jpg")) | set(media.glob("*.jpeg"))
    for img_file in sorted(all_imgs):
        data = img_file.read_bytes()
        new_name = f"img_{hashlib.sha1(data).hexdigest()[:8]}{img_file.suffix}"
        dest = dest_dir / new_name
        # 同名 = 同内容 → 已在盘上就不必重写（幂等，且让 mtime 稳定）
        if not dest.exists():
            dest.write_bytes(data)
        escaped = re.escape(img_file.name)
        raw_text = re.sub(
            rf'!\[([^\]]*)\]\s*\([^)]*{escaped}\)',
            rf'![\1](assets/{new_name})',
            raw_text,
        )
        count += 1

    try:
        shutil.rmtree(media)
    except OSError:
        pass

    if count:
        log.info("图片持久化", count=count, dest=str(dest_dir))
    return raw_text


def _persist_cover_image(cover_url: str, doc_dir: Path, tx_id: str) -> str:
    """下载视频封面缩略图到 {doc_dir}/assets/，返回 /api/assets/{tx_id}/cover URL。"""
    if not cover_url or cover_url.startswith("/api/assets/"):
        return cover_url
    from app.utils.url_security import validate_url
    try:
        validate_url(cover_url)
    except ValueError as e:
        log.warning("封面 URL 安全校验失败", url=cover_url[:80], error=str(e)[:80])
        return cover_url
    try:
        dest_dir = doc_dir / "assets"
        dest_dir.mkdir(parents=True, exist_ok=True)
        ext = ".jpg"
        url_lower = cover_url.lower()
        if ".png" in url_lower:
            ext = ".png"
        elif ".webp" in url_lower:
            ext = ".webp"
        dest = dest_dir / f"cover{ext}"
        if dest.exists():
            return f"/api/assets/{tx_id}/cover"
        import requests as req
        resp = req.get(cover_url, headers={"User-Agent": "LiMo/1.0"}, timeout=15)
        if resp.status_code == 200:
            dest.write_bytes(resp.content)
            log.info("封面下载", url=cover_url[:80], dest=dest.name, bytes=len(resp.content))
            return f"/api/assets/{tx_id}/cover"
        else:
            log.warning("封面下载失败", http=resp.status_code, url=cover_url[:80])
            return cover_url
    except Exception as e:
        log.warning("封面下载异常", url=cover_url[:80], error=str(e)[:80])
        return cover_url


def _persist_original_image(raw_text: str, doc_dir: Path) -> None:
    """图片导入：将原图拷贝到 doc_dir/assets/original.png。"""
    try:
        meta = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        return
    if meta.get("type") != "image":
        return

    file_path = meta.get("file_path", "")
    src = Path(file_path)
    if not src.exists():
        log.warning("原图拷贝跳过: 源文件不存在", src=str(src))
        return

    dest_dir = doc_dir / "assets"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "original.png"
    if not dest.exists():
        shutil.copy2(src, dest)
        log.info("原图拷贝", src=src.name, dest=str(dest))


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
        log.info("流式写入完成", name=safe_name, mb=round(total / (1024 * 1024), 1))
    else:
        data = await file.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise _FileTooLarge(MAX_UPLOAD_BYTES)
        tmp_path.write_bytes(data)
    return tmp_path


def _discard_upload(tmp_path: Path | None) -> None:
    """删除**无人接管**的上传临时文件（P1-10）。幂等、不抛。

    ⚠️ 边界：只有「这次导入没有成功持久化」时才该调它。成功路径的 tmp 会被
    `_persist_ingest_result` 登记成 `source_file_path`（重新转录要用），
    **删了就等于砍掉一个既有功能**——这正是本函数不接受「一律删除」调用的原因。
    """
    if tmp_path is None:
        return
    try:
        tmp_path.unlink()
    except OSError:
        pass


async def _file_pipeline_input(
    file: UploadFile, content_length: str | None, store_root: Path,
) -> tuple[PipelineInput, Path]:
    """上传文件 → `(PipelineInput, tmp_path)`，**两个导入端点同源**（P1-10）。

    ⚠️ 收成一份的原因：此前 `ingest`（:639-662）与 `ingest_stream`（:839-844）
    **各有一份逐字同构**的写盘逻辑，只差错误类型（`AppError` vs `_FileTooLarge`）。
    改一处漏一处**零报错**——它写的还是同一个磁盘路径，两边看起来都「在工作」。
    这是模式 #54（同源是搜索题不是记忆题）的又一形态。
    """
    try:
        tmp_path = await _handle_file_upload(file, content_length, store_root)
    except _FileTooLarge:
        raise AppError(
            "INGEST_FILE_TOO_LARGE",
            f"文件过大（最大 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB）",
            recoverable=False,
        ) from None
    return PipelineInput(type=InputType.FILE, file_path=str(tmp_path)), tmp_path


@router.post("/ingest")
async def ingest(
    request: Request,
    url: Optional[str] = Form(None),
    text: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    force: bool = Form(False),
    store=Depends(get_store),
    config=Depends(get_config),
):

    # ⚠️ `tmp_path` / `media_dir` 在**函数顶部无条件初始化**：下面的 `finally`
    # 是所有早退路径（无输入 / 不支持格式 / 适配器失败 / persist 抛异常）的唯一收口，
    # 它必须能在任何一个分支上安全读到这两个名字。
    tmp_path: Path | None = None
    media_dir: str | None = None
    persisted = False
    try:
        # 构造 PipelineInput
        if file:
            # 大小校验 + 写盘收口在 `_file_pipeline_input`（与流式端点同源，P1-10）
            inp, tmp_path = await _file_pipeline_input(
                file, request.headers.get("content-length"), store.root)
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

        # AppError（适配器自己报的业务错误）不包装，直接穿透到 finally
        try:
            output = await adapter.adapt(inp, engine_config=_build_engine_config(config, media_dir))
        except AppError:
            raise
        except Exception as e:
            log.exception("Ingest failed", error=str(e)[:200])
            raise AppError("INGEST_FAILED", f"导入失败: {str(e)[:200]}") from e

        result = await _persist_ingest_result(store, output, adapter.name, inp, media_dir)
        persisted = True
        if result.get("status") == "done" and "id" in result:
            _index_for_search(request, result["id"])
        return result
    finally:
        # ── 临时文件收口（P1-10）──
        # 原实现只在「适配器抛异常」那两支里删 tmp，于是三条路径**各漏一处**，
        # 而这三处都零报错、只体现为 `~/.local/…/tmp` 里悄悄涨：
        #   · 「不支持的格式」/「无输入」：tmp 已写盘，还没走到那个 except；
        #   · `_persist_ingest_result` 抛异常：在 try 之外（现已在 try 内）。
        # ⚠️ **成功路径不能删** —— `_persist_ingest_result` 已把 tmp 登记成
        # `source_file_path`（重新转录要用它）。
        if not persisted:
            _discard_upload(tmp_path)
        # media_dir（引擎写图的临时目录）：成功时 persist 里已 rmtree 过，幂等
        if media_dir:
            shutil.rmtree(media_dir, ignore_errors=True)



@router.post("/transcripts/{tx_id}/retranscribe")
async def retranscribe(tx_id: str, request: Request, store=Depends(get_store), config=Depends(get_config), guard=Depends(get_guard)) -> dict[str, Any]:
    """重新转录 — 用当前引擎绑定重新处理原始来源

    - URL 导入：重新下载 + 转录
    - 本地文件：直接引用存储的路径（不复制），重新转录
    """
    # 重转录用本地信号量（不抢占生成/处理的云端槽位）
    async with guard.local_semaphore:
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
        async with disconnect_watcher(request, cancel_event):
            try:
                engine_cfg = _build_engine_config(config, media_dir)
                engine_cfg["cancel_event"] = cancel_event
                output = await adapter.adapt(inp, engine_config=engine_cfg)
            except asyncio.CancelledError:
                raise AppError("RETX_CANCELLED", "操作已取消", recoverable=True)
            except AppError:
                raise
            except Exception as e:
                log.exception("Re-transcribe failed", error=str(e)[:200])
                raise AppError("RETX_FAILED", f"重新转录失败: {str(e)[:200]}") from e

        # 持久化引擎提取的图片，重写 rawText 中的路径
        doc_dir = store.get_doc_dir(tx_id)
        output.raw_text = _persist_media_images(output.raw_text, media_dir, doc_dir)
        _persist_original_image(output.raw_text, doc_dir)

        # 图片类型：原图就是封面（retranscribe 补上）
        if not tx.get("metadata", {}).get("cover_url") and adapter.name == "image":
            tx.setdefault("metadata", {})["cover_url"] = f"/api/files/{tx_id}/original.png"

        # 视频类型：封面下载到本地（新数据或旧数据首次转本地；to_thread 释放事件循环）
        cover_url = tx.get("metadata", {}).get("cover_url", "")
        if cover_url and not cover_url.startswith("/api/files/"):
            cover_url = await asyncio.to_thread(_persist_cover_image, cover_url, doc_dir, tx_id)
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
        # ⚠️ 返回值必须查（P1-06）：重新转录已经花掉时间与额度，写盘失败却回 ok =
        # 用户看到「转录完成」而 rawText 仍是旧的，刷新即回退，且**无从解释**。
        # 与上面 RETX_EMPTY 同一处置（失败显式化，不静默降级）。
        if not store.save_transcript_full(tx):
            raise AppError(
                "RETX_SAVE_FAILED",
                f"重新转录已成功（{output.engine}），但保存失败：文档目录不存在。旧内容已保留。",
                recoverable=False,
            )
        _index_for_search(request, tx_id)

        log.info("重新转录完成", title=tx.get("title", tx_id)[:40],
                 engine=output.engine, chars=len(output.raw_text))
        return {
            "status": "ok",
            "id": tx_id,
            "engine": output.engine,
        }


@router.post("/ingest/stream")
async def ingest_stream(
    request: Request,
    store=Depends(get_store),
    config=Depends(get_config),
    url: Optional[str] = Form(None),
    text: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    force: bool = Form(False),
):
    """SSE 流式导入 — 实时推送进度"""

    # ⚠️ `tmp_path` 在顶部无条件初始化：下面两处**早退**（无输入 / 格式不支持）
    # 都要能安全地读到它（`_discard_upload` 对 None 是 no-op）。
    tmp_path: Path | None = None
    if file:
        # 大小校验 + 写盘收口（与 `ingest` 同源，P1-10）
        inp, tmp_path = await _file_pipeline_input(
            file, request.headers.get("content-length"), store.root)
    elif url:
        inp = PipelineInput(type=InputType.URL, url=url)
    elif text:
        inp = PipelineInput(type=InputType.TEXT, text=text)
    else:
        raise AppError("INGEST_NO_INPUT", "请提供 url / text / file 之一", recoverable=False)

    adapter = AdapterRegistry.route(inp)
    if not adapter:
        # 早退路径：管道还没跑，上传的 tmp 无人接管 → 就地清理（P1-10）
        _discard_upload(tmp_path)
        raise AppError("INGEST_UNSUPPORTED", "不支持的输入格式",
                       detail=", ".join(a.name for a in AdapterRegistry.all()), recoverable=False)

    # 注入平台 Cookie + URL 去重
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
        # 客户端断开/Slow Consumer 导致 queue 满属于预期行为，仅记 warning 不抛异常
        except asyncio.QueueFull:
            log.warning("SSE queue full, dropping progress event (client may have disconnected)")

    async def run_pipeline():
        """在后台运行适配器管道（P1-10：整段一次 try/finally 收口）。

        此前有三处缺口，共同症状都是**没有任何报错**：
          ① `_persist_ingest_result` 在 try **之外** —— 它抛异常时 queue 永远收不到
             done/error，消费侧只能干等到 900s 静默超时才报「处理超时」，而真正
             的异常连日志都没有；
          ② 适配器失败时上传的 tmp 文件（可达 2GB）**从不删除** ——
             实测在 `~/.limo/tmp` 累积了 83 个文件 / 221MB；
          ③ `media_dir` 无论成败都不清理（引擎抽的图留在系统临时目录）。
        ⚠️ 成功路径**不能删 tmp**：`_persist_ingest_result` 已把它登记为
        `source_file_path`（重新转录要用）。
        """
        persisted = False
        try:
            output = await adapter.adapt(inp, on_progress=on_progress,
                                          engine_config=_build_engine_config(config, media_dir))
            result = await _persist_ingest_result(store, output, adapter.name, inp, media_dir)
            persisted = True
            queue.put_nowait({"type": "done", **result})
        except AppError as e:
            queue.put_nowait({"type": "error", "message": e.message, "code": e.code})
        except Exception as e:
            log.exception("Ingest stream failed", error=str(e)[:200])
            queue.put_nowait({"type": "error", "message": str(e)})
        finally:
            # ① 未成功持久化 → 删上传的源文件（成功时它已被登记，不能删）
            if not persisted:
                _discard_upload(tmp_path)
            # ② media_dir 无论成败都清（成功时 persist 里已 rmtree 过，幂等）
            shutil.rmtree(media_dir, ignore_errors=True)
            # ③ 结束哨兵（照 `services/synthesis.py` 的做法）：无论走哪条分支——
            #    包括**任何**漏网的异常与 task 被取消——消费侧都能立刻退出，
            #    而不是把每个 bug 都表现成「15 分钟无响应」。
            queue.put_nowait(None)

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
                    # 结束哨兵：`run_pipeline` 的 finally 一定投递它。
                    # ⚠️ 必须**先判 None 再取 `data["type"]`** —— 不然这一行本身
                    # 就抛 TypeError，把「管道异常退出」变成另一个更难查的异常。
                    if data is None:
                        break
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
                log.info("客户端断开，已取消后台管线任务")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ══════════════════════════════════════════════════════════════════
# 图片文件服务 — 转录引擎提取的图片，按 tx_id 隔离
# ══════════════════════════════════════════════════════════════════

# ── 公共资产服务 helper（P2-12：serve_media_file / serve_asset 去重）──

# 图片服务白名单。P0-7：剔除 svg——FileResponse 以 image/svg+xml 直接返回，
# 浏览器导航 URL 时脚本同源执行（存储型 XSS）。数据目录实测 0 存量 svg；
# chart 产物是 PNG 不受影响；网页嵌入 svg 图 <img> 渲染本不执行脚本。
_ALLOWED_IMAGE_EXTS = ("png", "jpg", "jpeg", "gif", "webp")


def _validate_tx_id(tx_id: str) -> None:
    """tx_id 只能由安全字符组成（防路径注入）。违规抛 400。

    ⚠️ 判据走 `core/resource_id.is_valid_resource_id`（P1-11）——本函数此前自带一份
    **只有字符集、漏了长度上限**的正则：同一个 `tx_id`，`QueryService.resolve_doc_id`
    （`:234`）与 `files._check_tx_id` 都拒长度 > 64，只有这条文件服务路径放行。
    分叉的方向还恰好是「读路径严、这条松」，且**两侧都不报错**——只在某个超长 ID
    上表现为「一半端点 404、另一半能取到图」（模式 #54：同源是搜索题不是记忆题）。
    """
    if not is_valid_resource_id(tx_id):
        raise AppError("INGEST_INVALID_TX_ID", "无效的文档 ID",
                        status_code=400, recoverable=False)


def _check_asset_ext(filename: str) -> str:
    """校验扩展名在白名单内。返回小写扩展名；非法（含 svg）抛 400。"""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in _ALLOWED_IMAGE_EXTS:
        raise AppError("INGEST_UNSUPPORTED_EXT", f"不支持的文件格式: {ext or '无扩展名'}",
                        status_code=400, recoverable=False)
    return ext


def _check_asset_within(path: Path, base_dir: Path) -> FileResponse | None:
    """防穿越校验（resolve + relative_to）→ 存在则返回 FileResponse，否则 None。"""
    resolved = path.resolve()
    try:
        resolved.relative_to(base_dir.resolve())
    except ValueError:
        raise AppError("INGEST_PATH_TRAVERSAL", "路径穿越已拒绝",
                        status_code=403, recoverable=False)
    if resolved.exists():
        return FileResponse(str(resolved))
    return None


@router.get("/files/{tx_id}/{filename}")
async def serve_media_file(tx_id: str, filename: str, request: Request, store=Depends(get_store)):
    """访问文档图片。优先级: doc_dir/assets/ > files/{tx_id}/ > sourceFilePath。"""
    _validate_tx_id(tx_id)
    if not re.match(r'^[a-zA-Z0-9_\-.]+$', filename):
        raise AppError("INGEST_INVALID_FILENAME", "无效的文件名",
                        status_code=400, recoverable=False)
    _check_asset_ext(filename)

    data_root = Path(store.root).resolve()

    # 1. {doc_dir}/assets/{filename}
    doc_dir = store.get_doc_dir(tx_id)
    if doc_dir:
        r = _check_asset_within(doc_dir / "assets" / filename, data_root)
        if r:
            return r

    # 2. files/{tx_id}/{filename}
    r = _check_asset_within(Path(store.root) / "files" / tx_id / filename, data_root)
    if r:
        return r

    # 3. Fallback: sourceFilePath from index (image imports)
    for entry in store._index.get("transcripts", []):
        if entry.get("id") == tx_id:
            src = entry.get("sourceFilePath", "")
            if src:
                r = _check_asset_within(Path(src), data_root)
                if r:
                    return r
            break

    raise AppError("INGEST_FILE_NOT_FOUND", "文件不存在",
                    status_code=404, recoverable=True)


# Asset file serving endpoint
@router.get("/assets/{tx_id}/{filename}")
async def serve_asset(tx_id: str, filename: str, store=Depends(get_store)):
    """Serve from {doc_dir}/assets/ — screenshots, covers, embedded images."""
    _validate_tx_id(tx_id)
    # filename 允许含 / 与 ..（图片在 assets 子目录中），仅限安全字符
    if not re.match(r'^[a-zA-Z0-9_\-./]+$', filename):
        raise AppError("INGEST_INVALID_FILENAME", "无效的文件名",
                        status_code=400, recoverable=False)

    # 封面 URL 无扩展名（/api/assets/{tx_id}/cover，_persist_cover_image 返回格式）
    # → 兜底查找 cover.{jpg,png,webp}
    candidates = [filename]
    if "." not in filename and filename == "cover":
        candidates = [f"cover{e}" for e in (".jpg", ".png", ".webp")]

    doc_dir = store.get_doc_dir(tx_id)
    if not doc_dir:
        raise AppError("INGEST_DOC_NOT_FOUND", "文档不存在",
                        status_code=404, recoverable=True)

    for cand in candidates:
        _check_asset_ext(cand)

        # ⚠️ 校验必须限定在本文档的 assets/ 内，而非整个 data_root —
        # filename 允许含 / 与 ..，只限 data_root 时越出 doc_dir/assets 可读任意文档图片
        r = _check_asset_within(doc_dir / "assets" / cand, doc_dir / "assets")
        if r:
            return r

        # Fallback: files/{tx_id}/{filename} (legacy) — 同样限定在 {tx_id} 子目录内
        r = _check_asset_within(Path(store.root) / "files" / tx_id / cand, Path(store.root) / "files" / tx_id)
        if r:
            return r

    raise AppError("INGEST_FILE_NOT_FOUND", "文件不存在",
                    status_code=404, recoverable=True)

