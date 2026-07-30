"""
生成路由 — POST /api/generate/{transcript_id}
对齐 BiliNote: 默认生成结构化笔记，可选额外生成整理版原文，两产物独立
"""

import logging
from typing import Optional
import asyncio
import json
import os
from contextlib import asynccontextmanager

# ── 活跃生成任务追踪（用于 get_markdown 返回 has_active_task）──
_active_generations: set[str] = set()


@asynccontextmanager
async def _track_generation(transcript_id: str):
    """追踪活跃的生成任务，get_markdown 据此返回 has_active_task"""
    _active_generations.add(transcript_id)
    try:
        yield
    finally:
        _active_generations.discard(transcript_id)
import re
import uuid

from fastapi import APIRouter, Request, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.types import MarkdownView, Style, GenerateMode
from app.core.dependencies import get_store, get_config, get_guard
from app.core.errors import GenerateError, AppError
from app.core.disconnect import disconnect_watcher  # 用于重构: 替换下方的 _watch_disconnect
from app.core import logging as log
from app.core.model_resolver import resolve_model as _resolve_model, resolve_llm_config
from app.llm import LLMService, CancelledError
from app.generation.generator import Generator, _post_process_content_markers, _post_process_screenshots
from app.generation.content_preparer import ContentPreparer
from app.generation.prompt_builder import PromptBuilder
from app.generation.prompt import build_polish_prompt, build_notes_prompt

logger = logging.getLogger(__name__)
router = APIRouter()


async def _watch_disconnect(request: Request, cancel_event: asyncio.Event, interval: float = 0.5) -> None:
    """后台轮询客户端断连，断开时 set cancel_event"""
    while not cancel_event.is_set():
        if await request.is_disconnected():
            cancel_event.set()
            return
        await asyncio.sleep(interval)


# ── 类型门控：哪些字段属于哪种 source_type ──
_TYPE_FIELDS: dict[str, set[str]] = {
    "video": {"video_understanding", "video_interval", "grid_cols", "grid_rows",
              "dedupe_enabled", "unit_width", "unit_height", "save_quality",
              "frame_quality_filter", "image_analyze", "image_optimize"},
    "document": {"image_analyze", "image_optimize"},  # PDF/Word/PPT 共享
    "epub": {"use_skeleton", "chapter_indices"},
}

# 不属于该类型的字段会被静默忽略（向后兼容）
def _warn_mismatched_fields(source_type: str, values: dict):
    own_fields = _TYPE_FIELDS.get(source_type, set())
    for st, fields in _TYPE_FIELDS.items():
        if st == source_type:
            continue
        for f in fields:
            if f in own_fields:
                continue  # 共享字段，跳过（如 image_analyze 同时属于 video 和 document）
            val = values.get(f)
            if val and val is not False and val != [] and val != 0:
                log.warning(
                    f"GenerateRequest source_type={source_type} 收到 {st} 专属字段 {f}={val}，已忽略")


class GenerateRequest(BaseModel):
    mode: str = "notes"  # 逗号分隔的产物列表："notes,polished"
    style: str = "detailed"
    formats: list[str] = []  # ["toc", "link", "screenshot", "summary", "images"]
    model_id: int | None = None
    user_notes: str = ""  # 用户备注
    source_type: str = ""  # 文档类型，用于校验字段。空字符串 = 不校验
    # VL 视觉理解参数（source_type=video 专属）
    video_understanding: bool = False
    video_interval: int = 6
    grid_cols: int = 2
    grid_rows: int = 2
    dedupe_enabled: bool = True
    unit_width: int = 960
    unit_height: int = 540
    save_quality: int = 90
    frame_quality_filter: bool = True
    # 文档图片参数（source_type=document 专属）
    image_analyze: bool = False
    image_optimize: bool = False
    # OCR 预处理（source_type=document 专属，与 image_analyze 互斥）
    ocr_enabled: bool = False
    ocr_model_id: int | None = None
    # EPUB 参数（source_type=epub 专属）
    use_skeleton: bool = False
    chapter_indices: list[int] = []


def _prepare_epub_text(raw_text: str, use_skeleton: bool, chapter_indices: list[int]) -> str:
    """EPUB 章节 JSON → 提取骨架或选中章节全文"""
    import json
    try:
        data = json.loads(raw_text)
        chapters = data.get("chapters", [])
    except (json.JSONDecodeError, TypeError):
        return raw_text

    if not chapters:
        return raw_text

    parts: list[str] = []
    for ch in chapters:
        sections = ch.get("sections", [])
        for sec in sections:
            idx = sec.get("index", -1)
            if chapter_indices and idx not in chapter_indices:
                continue
            if use_skeleton:
                text = sec.get("skeleton", "")
            else:
                text = sec.get("text", "")
            if text:
                title = ch.get("title", "")
                sec_title = sec.get("title", "")
                if sec_title:
                    parts.append(f"## {title} — {sec_title}\n\n{text}")
                else:
                    parts.append(f"## {title}\n\n{text}")

    if parts:
        return "\n\n---\n\n".join(parts)
    return raw_text


@router.post("/generate/{transcript_id}")
async def generate(transcript_id: str, req: GenerateRequest, request: Request) -> dict[str, object]:
    """生成 Markdown — 结构化笔记 + 可选的整理版原文"""
    guard = request.app.state.guard
    async with guard.semaphore:
        store = request.app.state.store
        config = request.app.state.config
        llm_config = resolve_llm_config(config, req.model_id)
        # ── LLMService ──
        llm = LLMService(config, model_id=req.model_id)

        tx = store.get_transcript(transcript_id)
        if not tx:
            raise AppError("GEN_NOT_FOUND", "文档不存在，请先导入内容")
        if not llm_config.get("api_key"):
            raise AppError("GEN_NO_API_KEY", "LLM API Key 未配置，请在设置中填写", recoverable=False)

        vl_config = None
        if req.video_understanding:
            source_url = tx.get("sourceUrl", "")
            # 视频下载 cookie：根据域名选择对应的平台 cookie
            cookies = ""
            auth = config.get("auth", {})
            url_lower = source_url.lower() if source_url else ""
            if "bilibili" in url_lower:
                cookies = auth.get("bilibili", "")
            elif "youtube" in url_lower or "youtu.be" in url_lower:
                cookies = auth.get("youtube", "")

            vl_config = {
                "video_understanding": True,
                "video_interval": req.video_interval,
                "grid_cols": req.grid_cols,
                "grid_rows": req.grid_rows,
                "dedupe_enabled": req.dedupe_enabled,
                "unit_width": req.unit_width,
                "unit_height": req.unit_height,
                "save_quality": req.save_quality,
                "quality_threshold": 0.15 if req.frame_quality_filter else 0.0,
                "source_url": source_url,
                "cookies": cookies,
            }

        # EPUB 章节预处理：骨架模式/选中章节
        epub_text = tx["rawText"]
        if req.use_skeleton or req.chapter_indices:
            epub_text = _prepare_epub_text(tx["rawText"], req.use_skeleton, req.chapter_indices)

        source_type = tx.get("sourceType", "text")
        if req.source_type:
            _warn_mismatched_fields(req.source_type, req.model_dump())

        # ── 解析 mode 为产物列表（逗号分隔）──
        _raw_mode = req.mode if req.mode else ""
        if _raw_mode == "both":
            _raw_mode = "notes,polished"
        mode_list = [m.strip() for m in _raw_mode.split(",") if m.strip()] if _raw_mode else []
        want_notes = "notes" in mode_list
        want_polished = "polished" in mode_list

        log.info("Generate start", tx_id=transcript_id, mode=mode_list,
                 style=req.style, source_type=source_type,
                 model=llm_config.get("provider", "?"))
        _active_generations.add(transcript_id)
        try:
            preparer = ContentPreparer(str(store.root), llm_config)
            prompt_builder = PromptBuilder(target_language=config.get("target_language", "zh-CN"))
            gen = Generator(llm, preparer, prompt_builder)

            # ── 截图用视频下载（非流式端点也需要，对标流式端点 line 376-396）──
            screenshot_video_path: str | None = None
            if "screenshot" in req.formats and source_type == "video" and not req.video_understanding:
                source_url = tx.get("sourceUrl", "")
                if source_url:
                    logger.info("原片截图：开始下载视频 %s", source_url[:80])
                    cookies = ""
                    auth = config.get("auth", {})
                    url_lower = source_url.lower()
                    if "bilibili" in url_lower:
                        cookies = auth.get("bilibili", "")
                    elif "youtube" in url_lower or "youtu.be" in url_lower:
                        cookies = auth.get("youtube", "")
                    loop = asyncio.get_event_loop()
                    screenshot_video_path = await loop.run_in_executor(
                        None, ContentPreparer._download_video, source_url, cookies)

            # ── 按产物列表分发 ──
            notes_mv: MarkdownView | None = None
            polished_mv: MarkdownView | None = None

            if want_notes and want_polished:
                polished_mv, notes_mv = await gen.generate_both(
                    raw_text=epub_text, title=tx.get("title", ""), style=req.style,
                    source_type=tx.get("sourceType", "text"), formats=req.formats,
                    vl_config=vl_config, user_notes=req.user_notes,
                    image_analyze=req.image_analyze, image_optimize=req.image_optimize,
                    ocr_enabled=req.ocr_enabled, ocr_model_id=req.ocr_model_id,
                )
                # 非流式端点截图后处理（generator 内部 video_path 可能为 None，此处补处理）
                if screenshot_video_path:
                    store_root = str(store.root)
                    polished_mv.content = _post_process_screenshots(
                        polished_mv.content, screenshot_video_path, store_root=store_root)
                    notes_mv.content = _post_process_screenshots(
                        notes_mv.content, screenshot_video_path, store_root=store_root)
                polished_mv.transcript_id = transcript_id
                notes_mv.transcript_id = transcript_id
                store.save_markdown(polished_mv)
                _auto_title(store, transcript_id, polished_mv.content)
                store.save_markdown(notes_mv)
                log.info("Generate both done", tx_id=transcript_id,
                         polished_chars=len(polished_mv.content), notes_chars=len(notes_mv.content))
            elif want_polished:
                polished_mv = await gen.generate(
                    raw_text=epub_text, title=tx.get("title", ""), style=req.style,
                    source_type=tx.get("sourceType", "text"), mode="polished",
                    formats=req.formats, vl_config=vl_config, user_notes=req.user_notes,
                    image_analyze=req.image_analyze, image_optimize=req.image_optimize,
                    ocr_enabled=req.ocr_enabled, ocr_model_id=req.ocr_model_id,
                )
                if screenshot_video_path:
                    polished_mv.content = _post_process_screenshots(
                        polished_mv.content, screenshot_video_path,
                        store_root=str(store.root))
                polished_mv.transcript_id = transcript_id
                store.save_markdown(polished_mv)
                _auto_title(store, transcript_id, polished_mv.content)
                log.info("Generate done", tx_id=transcript_id, mode="polished", chars=len(polished_mv.content))
            elif want_notes:
                notes_mv = await gen.generate(
                    raw_text=epub_text, title=tx.get("title", ""), style=req.style,
                    source_type=tx.get("sourceType", "text"), mode="notes",
                    formats=req.formats, vl_config=vl_config,
                    user_notes=req.user_notes,
                    image_analyze=req.image_analyze, image_optimize=req.image_optimize,
                    ocr_enabled=req.ocr_enabled, ocr_model_id=req.ocr_model_id,
                )
                if screenshot_video_path:
                    notes_mv.content = _post_process_screenshots(
                        notes_mv.content, screenshot_video_path,
                        store_root=str(store.root))
                notes_mv.transcript_id = transcript_id
                store.save_markdown(notes_mv)
                log.info("Generate done", tx_id=transcript_id, mode="notes", chars=len(notes_mv.content))
            else:
                raise AppError("GEN_INVALID_MODE", f"无法识别的生成模式: {req.mode}", recoverable=False)

            return {
                "id": notes_mv.id if notes_mv else (polished_mv.id if polished_mv else ""),
                "polished_id": polished_mv.id if polished_mv else None,
                "transcriptId": transcript_id,
                "content": notes_mv.content if notes_mv else None,
                "polished_content": polished_mv.content if polished_mv else None,
                "style": req.style,
                "mode": req.mode,
                "version": notes_mv.version if notes_mv else (polished_mv.version if polished_mv else 0),
            }
        except AppError:
            raise  # AppError 直接抛给中间件
        except Exception as e:
            log.exception("Generate failed", tx_id=transcript_id, error=str(e)[:200])
            raise GenerateError.api_error(detail=str(e)[:200]) from e
        finally:
            _active_generations.discard(transcript_id)


# ── 流式 SSE 端点 ──

class GenerateStreamRequest(BaseModel):
    """GET /generate/{id}/stream 的查询参数（用 Pydantic 校验）"""
    mode: str = "both"  # "notes" | "polished" | "both"
    style: str = "detailed"
    formats: str = ""  # 逗号分隔
    model_id: int | None = None
    user_notes: str = ""
    # VL 参数
    video_understanding: bool = False
    video_interval: int = 6
    grid_cols: int = 2
    grid_rows: int = 2
    dedupe_enabled: bool = True
    unit_width: int = 960
    unit_height: int = 540
    save_quality: int = 90
    frame_quality_filter: bool = True
    # EPUB 参数
    use_skeleton: bool = False
    chapter_indices: str = ""  # 逗号分隔的数字
    # 续写参数
    continue_from: str = ""  # "" = 新生成, "partial" = 续写最新残缺产物


@router.get("/generate/{transcript_id}/stream")
async def generate_stream(transcript_id: str, request: Request,
                          mode: str = "both", style: str = "detailed",
                          formats: str = "", model_id: int | None = None,
                          user_notes: str = "",
                          video_understanding: bool = False,
                          video_interval: int = 6, grid_cols: int = 2,
                          grid_rows: int = 2, dedupe_enabled: bool = True,
                          unit_width: int = 960, unit_height: int = 540,
                          save_quality: int = 90, frame_quality_filter: bool = True,
                          use_skeleton: bool = False, chapter_indices: str = "",
                          image_analyze: bool = False,
                          image_optimize: bool = False,
                          ocr_enabled: bool = False,
                          ocr_model_id: int | None = None,
                          continue_from: str = ""):
    """流式生成笔记——SSE 响应，实时推送每个 chunk。

    取消逻辑：前端 abort() → SSE 断开 → CancelledError → 保存残缺产物(partial)。
    续写：continue_from=partial → 加载残缺产物 → 拼"继续"上下文 → 流式。
    """
    store = request.app.state.store
    config = request.app.state.config

    tx = store.get_transcript(transcript_id)
    if not tx:
        raise AppError("GEN_NOT_FOUND", "文档不存在，请先导入内容")

    # 校验 style 有效性（Polish only 模式可能传 "none"）
    _valid_styles = {s.value for s in Style}
    if style not in _valid_styles:
        style = "detailed"

    # ── 解析 mode 为产物列表（逗号分隔）──
    _raw_mode = mode if mode else ""
    # "both" → "notes,polished"（向后兼容）
    if _raw_mode == "both":
        _raw_mode = "notes,polished"
    mode_list = [m.strip() for m in _raw_mode.split(",") if m.strip()] if _raw_mode else []
    want_notes = "notes" in mode_list
    want_polished = "polished" in mode_list
    # 推导 stream_mode
    if want_notes and want_polished:
        stream_mode = "both"
    elif want_notes:
        stream_mode = "notes"
    elif want_polished:
        stream_mode = "polished"
    else:
        stream_mode = ""

    # ── 续写：加载残缺产物 ──
    partial_content = ""
    if continue_from == "partial":
        mds = store.get_markdowns_by_transcript(transcript_id)
        for md in mds:
            if md.get("status") == "partial":
                if md.get("mode", "") == stream_mode:
                    partial_content = store.get_markdown(md["id"]) or ""
                    break

    # ── LLMService ──
    llm = LLMService(config, model_id=model_id)
    if not llm.api_key:
        raise AppError("GEN_NO_API_KEY", "LLM API Key 未配置，请在设置中填写", recoverable=False)

    # ── 内容准备 + Prompt 构建 ──
    target_language = config.get("target_language", "zh-CN")
    prep_cfg = {
        "provider": llm.provider, "api_key": llm.api_key,
        "model": {llm.provider: llm.model},
        "base_url": {llm.provider: llm.base_url},
        "capabilities": llm.capabilities,
    }
    preparer = ContentPreparer(str(store.root), prep_cfg)
    prompt_builder = PromptBuilder(target_language=target_language)

    # VL 配置
    vl_config = None
    if video_understanding:
        source_url = tx.get("sourceUrl", "")
        cookies = ""
        auth = config.get("auth", {})
        url_lower = source_url.lower() if source_url else ""
        if "bilibili" in url_lower:
            cookies = auth.get("bilibili", "")
        elif "youtube" in url_lower or "youtu.be" in url_lower:
            cookies = auth.get("youtube", "")
        vl_config = {
            "video_understanding": True,
            "video_interval": video_interval,
            "grid_cols": grid_cols, "grid_rows": grid_rows,
            "dedupe_enabled": dedupe_enabled,
            "unit_width": unit_width, "unit_height": unit_height,
            "save_quality": save_quality,
            "quality_threshold": 0.15 if frame_quality_filter else 0.0,
            "source_url": source_url, "cookies": cookies,
        }

    epub_text = tx["rawText"]
    if use_skeleton or chapter_indices:
        ch_list = [int(x.strip()) for x in chapter_indices.split(",") if x.strip()]
        epub_text = _prepare_epub_text(tx["rawText"], use_skeleton, ch_list)

    format_list = [f.strip() for f in formats.split(",") if f.strip()]
    source_type = tx.get("sourceType", "text")

    # ── 截图用视频下载（不开启画面理解时也支持"插入原片截图"）──
    screenshot_video_path: str | None = None
    if "screenshot" in format_list and source_type == "video" and not video_understanding:
        source_url = tx.get("sourceUrl", "")
        if source_url:
            logger.info("原片截图：开始下载视频 %s", source_url[:80])
            cookies = ""
            auth = config.get("auth", {})
            url_lower = source_url.lower()
            if "bilibili" in url_lower:
                cookies = auth.get("bilibili", "")
            elif "youtube" in url_lower or "youtu.be" in url_lower:
                cookies = auth.get("youtube", "")
            import asyncio as _asyncio
            loop = _asyncio.get_event_loop()
            screenshot_video_path = await loop.run_in_executor(
                None, ContentPreparer._download_video, source_url, cookies)
            if screenshot_video_path:
                logger.info("原片截图：视频下载完成 %s", screenshot_video_path)
            else:
                logger.warning("原片截图：视频下载失败，截图将不可用")

    guard = request.app.state.guard
    cancel_event = asyncio.Event()

    async def merged_stream():
        await guard.semaphore.acquire()
        _active_generations.add(transcript_id)
        # 启动断连监听（后台轮询，客户端断开时 set cancel_event）
        _disconnect_task = asyncio.create_task(
            _watch_disconnect(request, cancel_event))
        try:
            all_chunks: list[str] = []

            # ── 双产物并行 ──
            if stream_mode == "both":
                keep_refs = "images" in format_list
                # 前端已保证门控：视频=videoUnderstanding，文档=formatImages&&imageAnalyze
                send_to_llm = bool(image_analyze)

                # OCR 预处理：图片→文字→塞回原文（在 prepare 之前）
                processed_text = epub_text
                if ocr_enabled and ocr_model_id:
                    from app.generation.ocr_preprocessor import OCRPreprocessor
                    ocr_engine = OCRPreprocessor(config, ocr_model_id)
                    processed_text = await ocr_engine.process(epub_text, str(store.root))

                # 预处理（VL + 图片剥离 + 截断）
                clean_text, image_urls, video_path = await preparer.prepare(
                    processed_text, vl_config, keep_refs, send_to_llm=send_to_llm, optimize_images=image_optimize)

                polished_prompt = build_polish_prompt(
                    clean_text, tx.get("title", ""), style, source_type,
                    formats=format_list, user_notes=user_notes,
                    target_language=target_language,
                    video_understanding=video_understanding)
                notes_prompt = build_notes_prompt(
                    clean_text, tx.get("title", ""), style, source_type,
                    formats=format_list, user_notes=user_notes,
                    target_language=target_language,
                    video_understanding=video_understanding)

                # 续写：把残缺内容加进去
                if partial_content:
                    polished_prompt = _build_continue_prompt(polished_prompt, partial_content)
                    notes_prompt = _build_continue_prompt(notes_prompt, partial_content)

                queue: asyncio.Queue = asyncio.Queue()

                async def stream_one(label: str, prompt: str):
                    try:
                        async for chunk in llm.stream(
                            prompt, image_urls=image_urls,
                            cancel_event=cancel_event,
                        ):
                            await queue.put({"mode": label, "text": chunk})
                        await queue.put({"mode": label, "type": "done"})
                    except CancelledError:
                        await queue.put({"mode": label, "type": "cancelled"})
                    except Exception as e:
                        await queue.put({
                            "mode": label, "type": "error",
                            "message": str(e)[:200],
                        })

                tasks = [
                    asyncio.create_task(stream_one("polished", polished_prompt)),
                    asyncio.create_task(stream_one("notes", notes_prompt)),
                ]

                done_count = 0
                polished_chunks: list[str] = []
                notes_chunks: list[str] = []
                was_cancelled = False
                polished_error: str | None = None
                notes_error: str | None = None

                while done_count < 2:
                    data = await queue.get()
                    etype = data.get("type", "")
                    if etype in ("done", "error", "cancelled"):
                        done_count += 1
                        if etype == "cancelled":
                            was_cancelled = True
                        elif etype == "error":
                            if data.get("mode") == "polished":
                                polished_error = data.get("message", "")
                            elif data.get("mode") == "notes":
                                notes_error = data.get("message", "")
                    if data.get("mode") == "polished" and "text" in data:
                        polished_chunks.append(data["text"])
                    elif data.get("mode") == "notes" and "text" in data:
                        notes_chunks.append(data["text"])
                    yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

                await asyncio.gather(*tasks, return_exceptions=True)

                # 保存双产物（取消时 status=partial，完成时 status=complete）
                status = "partial" if was_cancelled else "complete"
                if polished_chunks:
                    polished_content = _post_process(
                        "".join(polished_chunks), format_list,
                        video_path or screenshot_video_path, source_type, store.root)
                    mv = MarkdownView(
                        id=f"md_{uuid.uuid4().hex[:12]}",
                        transcript_id=transcript_id, content=polished_content,
                        mode=GenerateMode.POLISHED, style=Style(style), status=status,
                    )
                    store.save_markdown(mv)
                    _auto_title(store, transcript_id, mv.content)
                if notes_chunks:
                    notes_content = _post_process(
                        "".join(notes_chunks), format_list,
                        video_path or screenshot_video_path, source_type, store.root)
                    mv = MarkdownView(
                        id=f"md_{uuid.uuid4().hex[:12]}",
                        transcript_id=transcript_id, content=notes_content,
                        mode=GenerateMode.NOTES, style=Style(style), status=status,
                    )
                    store.save_markdown(mv)
                    _auto_title(store, transcript_id, mv.content)

                if video_path and os.path.exists(video_path):
                    try:
                        os.remove(video_path)
                    except OSError:
                        pass
                if screenshot_video_path and os.path.exists(screenshot_video_path):
                    try:
                        os.remove(screenshot_video_path)
                    except OSError:
                        pass

                # 根据双产物结果决定最终事件
                both_failed = polished_error is not None and notes_error is not None
                one_failed = polished_error is not None or notes_error is not None

                if both_failed and not was_cancelled:
                    yield f"data: {json.dumps({'type': 'error', 'message': 'Both polished and notes generation failed'}, ensure_ascii=False)}\n\n"
                elif one_failed and not was_cancelled:
                    failed_product = "polished" if polished_error else "notes"
                    error_msg = polished_error or notes_error
                    yield f"data: {json.dumps({'type': 'warning', 'mode': failed_product, 'message': f'{failed_product} generation failed: {error_msg}'}, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                else:
                    yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                return

            # ── 单产物 ──
            keep_refs = "images" in format_list
            send_to_llm = bool(image_analyze)

            # OCR 预处理
            processed_text = epub_text
            if ocr_enabled and ocr_model_id:
                from app.generation.ocr_preprocessor import OCRPreprocessor
                ocr_engine = OCRPreprocessor(config, ocr_model_id)
                processed_text = await ocr_engine.process(epub_text, str(store.root))

            clean_text, image_urls, video_path = await preparer.prepare(
                processed_text, vl_config, keep_refs, send_to_llm=send_to_llm, optimize_images=image_optimize)

            if stream_mode == "polished":
                prompt = build_polish_prompt(
                    clean_text, tx.get("title", ""), style, source_type,
                    formats=format_list, user_notes=user_notes,
                    target_language=target_language,
                    video_understanding=video_understanding)
            else:
                prompt = build_notes_prompt(
                    clean_text, tx.get("title", ""), style, source_type,
                    formats=format_list, user_notes=user_notes,
                    target_language=target_language,
                    video_understanding=video_understanding)

            if partial_content:
                prompt = _build_continue_prompt(prompt, partial_content)

            async for chunk in llm.stream(
                prompt, image_urls=image_urls, cancel_event=cancel_event,
            ):
                all_chunks.append(chunk)
                yield f"data: {json.dumps({'text': chunk}, ensure_ascii=False)}\n\n"

            # 完成后保存
            content = _post_process("".join(all_chunks), format_list,
                                    video_path or screenshot_video_path, source_type, store.root)
            mv_mode = GenerateMode.POLISHED if stream_mode == "polished" else GenerateMode.NOTES
            mv = MarkdownView(
                id=f"md_{uuid.uuid4().hex[:12]}",
                transcript_id=transcript_id, content=content,
                mode=mv_mode, style=Style(style),
            )
            if partial_content:
                # 续写完成 → 合并内容保存，标记 complete
                mv.status = "complete"
                mv.content = partial_content.rstrip() + "\n\n" + content
            store.save_markdown(mv)
            _auto_title(store, transcript_id, mv.content)
            yield f"data: {json.dumps({'type': 'done', 'id': mv.id}, ensure_ascii=False)}\n\n"

            if video_path and os.path.exists(video_path):
                try:
                    os.remove(video_path)
                except OSError:
                    pass
            if screenshot_video_path and os.path.exists(screenshot_video_path):
                try:
                    os.remove(screenshot_video_path)
                except OSError:
                    pass

        except CancelledError:
            # 取消 → 保存残缺产物
            if all_chunks:
                content = "".join(all_chunks)
                if partial_content:
                    content = partial_content.rstrip() + "\n\n" + content
                mv_mode = GenerateMode.POLISHED if stream_mode == "polished" else GenerateMode.NOTES
                mv = MarkdownView(
                    id=f"md_{uuid.uuid4().hex[:12]}",
                    transcript_id=transcript_id, content=content,
                    mode=mv_mode, style=Style(style), status="partial",
                )
                store.save_markdown(mv)
                _auto_title(store, transcript_id, mv.content)
                yield f"data: {json.dumps({'type': 'cancelled', 'saved_partial': True, 'id': mv.id}, ensure_ascii=False)}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'cancelled'}, ensure_ascii=False)}\n\n"
        except Exception as e:
            log.exception("Generate stream failed", tx_id=transcript_id)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)[:200]}, ensure_ascii=False)}\n\n"
        finally:
            # 清理断连监听
            _disconnect_task.cancel()
            try:
                await _disconnect_task
            except (asyncio.CancelledError, Exception):
                pass
            _active_generations.discard(transcript_id)
            guard.semaphore.release()

    return StreamingResponse(
        merged_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _build_continue_prompt(original_prompt: str, partial_content: str) -> str:
    """构建续写 prompt——将残缺内容作为 assistant 的已有输出"""
    return (
        f"{original_prompt}\n\n"
        f"---\n"
        f"以下是你之前生成的内容（被中断），请从断点处继续写：\n\n"
        f"{partial_content}\n\n"
        f"---\n"
        f"请从以上内容的断点处开始继续生成。不要重复已有内容，直接写剩余部分。"
    )


def _post_process(content: str, formats: list[str],
                  video_path, source_type: str, store_root: str = "") -> str:
    """后处理（对齐原有 Generator 行为）"""
    content = _post_process_content_markers(content)
    if "screenshot" in formats and video_path:
        content = _post_process_screenshots(content, str(video_path), store_root=store_root)
    return content


@router.get("/markdown/{transcript_id}")
async def get_markdown(transcript_id: str, mode: str = "", version: int = 0, request: Request = None) -> dict[str, object]:
    """获取指定 transcript 的生成结果。mode=polished 返回整理版原文，version=0 返回最新"""
    store = request.app.state.store
    mds = store.get_markdowns_by_transcript(transcript_id)
    if not mds:
        # 如果正在生成中，返回 has_active_task 让前端轮询
        if transcript_id in _active_generations:
            return {"has_active_task": True, "content": None, "versions": []}
        raise AppError("MD_NOT_FOUND", "尚未生成，请先点击生成按钮", status_code=404)

    # 按模式精确筛选（notes / polished / fact_check 各自独立，不混在一起）
    if mode:
        mode_mds = [md for md in mds if md.get("mode", "") == mode]
        if not mode_mds:
            raise AppError("MD_NOT_FOUND", f"尚未生成 {mode} 内容，请先点击生成按钮", status_code=404)
    else:
        mode_mds = mds

    # 选择版本（默认最新 = idx 0，因为 get_markdowns_by_transcript 已降序排列）
    selected = mode_mds[0]
    if version > 0:
        for md in mode_mds:
            if md.get("version") == version:
                selected = md
                break

    return {
        **selected,
        "versions": [{"version": m.get("version", 1), "id": m.get("id"),
                       "createdAt": m.get("createdAt"), "status": m.get("status", "complete")}
                      for m in mode_mds],
    }


def _extract_title(content: str) -> str | None:
    """从 Markdown 内容第一行提取 # 标题，15字以内的返回"""
    m = re.match(r'^#\s+(.+?)(?:\s*$|\n)', content)
    if m:
        t = m.group(1).strip()
        return t if len(t) <= 20 else None
    return None

def _auto_title(store: object, tx_id: str, content: str) -> None:
    """如果文档标题是自动生成的（URL/哈希/未命名），用生成内容的第一行标题替换"""
    tx = store.get_transcript(tx_id)
    if not tx:
        return
    old_title = tx.get("title", "")
    # 判断是否需要替换：URL 格式、纯哈希、未命名
    is_auto = (
        not old_title or
        old_title.startswith("http") or
        old_title.startswith("https") or
        re.match(r'^[a-f0-9]{32}', old_title) or
        old_title == "未命名" or
        "_" in old_title or           # 技术名：DSpark_paper
        len(old_title) > 40           # URL 截断或超长文本
    )
    if not is_auto:
        return
    new_title = _extract_title(content)
    if new_title:
        tx["title"] = new_title
        store.save_transcript_full(tx)
        logger.info("自动标题: %s → %s", old_title[:30], new_title)
