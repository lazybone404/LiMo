"""
生成路由 — POST /api/generate/{transcript_id}
对齐 BiliNote: 默认生成结构化笔记，可选额外生成整理版原文，两产物独立
"""

import asyncio
import json
import os
import re
from pathlib import Path

# ── 活跃生成任务追踪（用于 get_markdown 返回 has_active_task）──
_active_generations: set[str] = set()

import uuid

from fastapi import APIRouter, Request, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from datetime import datetime

from app.core.types import MarkdownView, Style, GenerateMode
from app.core.dependencies import get_store, get_config, get_guard
from app.core.errors import GenerateError, AppError
from app.core.reference_store import get_references
from app.core import logging as log
from app.core.model_resolver import resolve_llm_config
from app.llm import LLMService, CancelledError
from app.generation.generator import Generator, _post_process_content_markers, _post_process_screenshots
from app.services.chart_renderer import render_charts, CHART_FENCE_MARKS
from app.generation.content_preparer import ContentPreparer
from app.generation.prompt_builder import PromptBuilder
from app.generation.prompt import build_polish_prompt, build_notes_prompt
from app.storage.store import rewrite_asset_urls

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
    "video": {"video_understanding", "video_direct", "video_interval", "grid_cols", "grid_rows",
              "dedupe_enabled", "unit_width", "unit_height", "save_quality",
              "frame_quality_filter", "image_analyze", "image_optimize"},
    "document": {"image_analyze", "image_optimize"},  # PDF/Word/PPT 共享
}

# 不属于该类型的字段会被静默忽略（向后兼容）
# ── 生成超时（按是否勾选图表分档）──
# 图表任务的输出明显更长（1~N 张语义 JSON + 必要时拆图），需要更长的总超时窗口。
# 渲染本身**不在**这个窗口内 —— asyncio.timeout 只包住流式循环，render_charts 在其
# 退出之后经 asyncio.to_thread 执行。
#
# 定值依据（2026-09-19 实测，deepseek-v4-flash，约 900 字文档）：
#   勾选图表 → 输出 6 张图（hchain×3 / grid×2 / layers×1）/ 2890 字符 / **9.8s**
# 注意 **不要**按「P95 × 1.5」套用：该公式假设多图会显著拉长生成，而实测推翻了
# 这个前提——新协议下模型只写语义 JSON（每张 130~240 字符），不再手写数百行 SVG，
# 出图几乎不增加成本（按公式算得 15s，比基础档还短，显然无意义）。
# 真正的耗时主导项是**正文字符数**（长文档的笔记本身），图表只在其上叠加约 18%
# 的输出增量。故取基础档线性外推 + 安全余量：330 × 1.21 ≈ 400。
# 放宽只影响最坏等待时长，不影响正常路径；单 chunk 静默期的 150s 上限
# （下方 wait_for）独立生效，不随此值变化。
_GEN_TIMEOUT_BASE = 330.0
_GEN_TIMEOUT_CHART = 400.0


def _gen_timeout(formats) -> float:
    """按 formats 选总超时。format 可传 list 或逗号分隔字符串。"""
    if isinstance(formats, str):
        fmts = [f.strip() for f in formats.split(",") if f.strip()]
    else:
        fmts = list(formats or [])
    return _GEN_TIMEOUT_CHART if "chart" in fmts else _GEN_TIMEOUT_BASE


def _has_chart_fence(text: str) -> bool:
    """产物里是否含图表围栏（```fig 语义 JSON 或 ```chart 手写 SVG）。

    抽成函数是为了可测——此前是两处内联的 `"```chart" not in ...` 字符串判断，
    新增 fig 协议后必须两处同步改，漏一处就变成「明明出了图却报未生成」。
    """
    return any(mark in (text or "") for mark in CHART_FENCE_MARKS)


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
    reference_ids: list[str] = []  # 参考文件暂存 id（POST /api/references 取得，仅 notes 注入）
    source_type: str = ""  # 文档类型，用于校验字段。空字符串 = 不校验
    # VL 视觉理解参数（source_type=video 专属）
    video_understanding: bool = False
    video_direct: bool = False  # 视频直传模型（DashScope SDK，模型支持 video 时）
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


def _resolve_reference_texts(ids: list[str]) -> list[dict]:
    """参考文件暂存 id → [{name, content}]（TTL 内有效；失效/不存在的 id 跳过，不注入）。"""
    return get_references([r for r in ids if r])


@router.post("/generate/{transcript_id}")
async def generate(transcript_id: str, req: GenerateRequest, request: Request) -> dict[str, object]:
    """生成 Markdown — 结构化笔记 + 可选的整理版原文"""
    guard = request.app.state.guard
    async with guard.semaphore:
        store = request.app.state.store
        config = request.app.state.config
        llm_config = resolve_llm_config(config, req.model_id)
        # ── LLMService ──
        # 非流式路径没有 asyncio.timeout 包壳，SDK 的 httpx 超时是唯一闸门，
        # 所以多图场景必须在这里就放宽，否则照样撞 330。
        llm = LLMService(config, model_id=req.model_id,
                         request_timeout=_gen_timeout(req.formats))

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
                "video_direct": req.video_direct,
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

        # epub 一键解读走独立路由（/api/epub/generate），此处直传 rawText
        epub_text = tx["rawText"]

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
            preparer = ContentPreparer(str(store.root), llm_config,
                                       doc_dir=str(store._get_doc_dir(transcript_id) or ""))
            prompt_builder = PromptBuilder(target_language=config.get("target_language", "zh-CN"))
            gen = Generator(llm, preparer, prompt_builder, config=config)

            # ── 截图用视频下载（非流式端点也需要，对标流式端点 line 376-396）──
            screenshot_video_path: str | None = None
            if "screenshot" in req.formats and source_type == "video" and not req.video_understanding:
                source_url = tx.get("sourceUrl", "")
                if source_url:
                    log.info("原片截图：开始下载视频", url=source_url[:80])
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
                    reference_texts=_resolve_reference_texts(req.reference_ids),
                    image_analyze=req.image_analyze, image_optimize=req.image_optimize,
                    ocr_enabled=req.ocr_enabled, ocr_model_id=req.ocr_model_id,
                    video_direct=req.video_direct,
                )
                # 非流式端点截图后处理（generator 内部 video_path 可能为 None，此处补处理）
                if screenshot_video_path:
                    doc_dir = str(store._get_doc_dir(transcript_id) or "")
                    polished_mv.content = _post_process_screenshots(
                        polished_mv.content, screenshot_video_path, doc_dir=doc_dir)
                    notes_mv.content = _post_process_screenshots(
                        notes_mv.content, screenshot_video_path, doc_dir=doc_dir)
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
                    video_direct=req.video_direct,
                )
                if screenshot_video_path:
                    polished_mv.content = _post_process_screenshots(
                        polished_mv.content, screenshot_video_path,
                        doc_dir=str(store._get_doc_dir(transcript_id) or ""))
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
                    reference_texts=_resolve_reference_texts(req.reference_ids),
                    image_analyze=req.image_analyze, image_optimize=req.image_optimize,
                    ocr_enabled=req.ocr_enabled, ocr_model_id=req.ocr_model_id,
                    video_direct=req.video_direct,
                )
                if screenshot_video_path:
                    notes_mv.content = _post_process_screenshots(
                        notes_mv.content, screenshot_video_path,
                        doc_dir=str(store._get_doc_dir(transcript_id) or ""))
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
            # 关闭 LLM 连接池（每请求一 LLMService，防 httpx client 常驻累积——
            # 对齐流式端点 P1-7 出口规范，非流式端点此前漏 aclose）
            try:
                await llm.aclose()
            except Exception:
                pass


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
    video_direct: bool = False  # 视频直传模型（DashScope SDK，模型支持 video 时）
    video_interval: int = 6
    grid_cols: int = 2
    grid_rows: int = 2
    dedupe_enabled: bool = True
    unit_width: int = 960
    unit_height: int = 540
    save_quality: int = 90
    frame_quality_filter: bool = True
    # 续写参数
    continue_from: str = ""  # "" = 新生成, "partial" = 续写最新残缺产物


@router.get("/generate/{transcript_id}/stream")
async def generate_stream(transcript_id: str, request: Request,
                          mode: str = "both", style: str = "detailed",
                          formats: str = "", model_id: int | None = None,
                          user_notes: str = "", reference_ids: str = "",
                          video_understanding: bool = False,
                          video_direct: bool = False,
                          video_interval: int = 6, grid_cols: int = 2,
                          grid_rows: int = 2, dedupe_enabled: bool = True,
                          unit_width: int = 960, unit_height: int = 540,
                          save_quality: int = 90, frame_quality_filter: bool = True,
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

    # 参考文件：逗号分隔的暂存 id → 内容（仅 notes 注入；失效 id 自动跳过）
    ref_texts = _resolve_reference_texts(reference_ids.split(",") if reference_ids else [])

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
    # format_list 上移到构造之前：request_timeout 要按是否含 chart 分档。
    format_list = [f.strip() for f in formats.split(",") if f.strip()]
    llm = LLMService(config, model_id=model_id,
                     request_timeout=_gen_timeout(format_list))
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
    preparer = ContentPreparer(str(store.root), prep_cfg,
                               doc_dir=str(store._get_doc_dir(transcript_id) or ""))
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
            "video_direct": video_direct,
            "video_interval": video_interval,
            "grid_cols": grid_cols, "grid_rows": grid_rows,
            "dedupe_enabled": dedupe_enabled,
            "unit_width": unit_width, "unit_height": unit_height,
            "save_quality": save_quality,
            "quality_threshold": 0.15 if frame_quality_filter else 0.0,
            "source_url": source_url, "cookies": cookies,
        }

    epub_text = tx["rawText"]

    source_type = tx.get("sourceType", "text")
    log.info("生成开始", source_type=source_type, formats=",".join(format_list))

    # ── 截图用视频下载（不开启画面理解时也支持"插入原片截图"）──
    screenshot_video_path: str | None = None
    if "screenshot" in format_list and source_type == "video" and not video_understanding:
        source_url = tx.get("sourceUrl", "")
        if source_url:
            log.info("原片截图：开始下载视频", url=source_url[:80])
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
                log.info("原片截图：视频下载完成", path=screenshot_video_path)
            else:
                log.warning("原片截图：视频下载失败，截图将不可用")

    guard = request.app.state.guard
    cancel_event = asyncio.Event()

    async def merged_stream():
        await guard.semaphore.acquire()
        _active_generations.add(transcript_id)
        # 未完成标记：生成中断（应用重启/崩溃）时残留，capabilities 检测后前端提示
        marker_path: Path | None = None
        _doc_dir = store._get_doc_dir(transcript_id)
        if _doc_dir:
            marker_path = _doc_dir / ".generating"
            try:
                marker_path.write_text(
                    json.dumps({"tx_id": transcript_id,
                                "started_at": datetime.now().isoformat()}),
                    encoding="utf-8")
            except OSError:
                marker_path = None
        # 启动断连监听（后台轮询，客户端断开时 set cancel_event）
        _disconnect_task = asyncio.create_task(
            _watch_disconnect(request, cancel_event))
        try:
            all_chunks: list[str] = []
            video_path: str | None = None  # P0-5：finally 统一清理（prepare 失败时未定义，需预初始化）
            # P1-8：双产物协程引用（断连/异常时 finally 取消，防孤儿任务继续烧 token）
            _tasks: list[asyncio.Task] = []

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
                    processed_text = await ocr_engine.process(epub_text, str(store.root), doc_dir=str(store._get_doc_dir(transcript_id) or ""))

                # 预处理（VL + 图片剥离 + 截断）
                clean_text, image_urls, video_path = await preparer.prepare(
                    processed_text, vl_config, keep_refs, send_to_llm=send_to_llm,
                    optimize_images=image_optimize, video_direct=video_direct)

                polished_prompt = build_polish_prompt(
                    clean_text, tx.get("title", ""), style, source_type,
                    formats=format_list, user_notes=user_notes,
                    target_language=target_language,
                    video_understanding=video_understanding)
                notes_prompt = build_notes_prompt(
                    clean_text, tx.get("title", ""), style, source_type,
                    formats=format_list, user_notes=user_notes,
                    target_language=target_language,
                    video_understanding=video_understanding,
                    reference_texts=ref_texts)

                # 续写：把残缺内容加进去
                if partial_content:
                    polished_prompt = _build_continue_prompt(polished_prompt, partial_content)
                    notes_prompt = _build_continue_prompt(notes_prompt, partial_content)

                # ── 并行生成（notes ∥ polished）──
                # 历史：曾改串行，因 deepseek 对并发请求排队（并行时第二个响应 108s+）。
                # 用户实测 notes 快（~35s）、polished 慢（deepseek 长输出断流，服务端
                # 稳定性问题，与串/并行无关）——并行时两路同时开始，总耗时 = max 而非
                # 叠加，且 notes 先出、体验渐进。45s 断流判定 + 2 次尝试保留。
                # 两路事件经 asyncio.Queue 汇合（chunk 交错到达），保证 SSE 顺序安全。
                was_cancelled = False
                polished_error: str | None = None
                notes_error: str | None = None
                _queue: asyncio.Queue[dict] = asyncio.Queue()

                async def _run_product(label: str, prompt: str, reasoning_effort: str | None = None) -> tuple[list[str], bool, str | None]:
                    """单产物：最多 2 次尝试（首试 + 1 次重试），事件经 queue 汇合。
                    返回 (chunks, cancelled, error_msg)。reasoning_effort：思考强度
                    （deepseek-v4 专用，polished 传 low）。"""
                    chunks: list[str] = []
                    err_msg: str | None = None
                    cancelled = False
                    if cancel_event and cancel_event.is_set():
                        cancelled = True
                        await _queue.put({"mode": label, "type": "cancelled", "saved_partial": False})
                        return chunks, cancelled, None

                    async def _reasoning_cb(text: str):
                        # 思考链 → SSE 事件（前端 ProcessingPlaceholder 展示"模型思考中"）
                        await _queue.put({"mode": label, "type": "reasoning", "text": text})

                    for attempt in range(2):
                        try:
                            # 总超时按 formats 分档（_gen_timeout：勾选图表时放宽到
                            # _GEN_TIMEOUT_CHART）+ chunk 间隔超时 150s：
                            # deepseek-v4 思考模式（文档：默认启用，effort=high）思考阶段
                            # 服务端静默不推流（实测首 chunk 后 90s 零数据）——45s 判定会
                            # 把正常长思考误判为挂起。150s 容忍长思考；推理链 chunk 到达
                            # 时 anext 正常返回（有 reasoning 也在推流），不触发超时。
                            # 两个超时互相独立：总超时管整体，150s 管单 chunk 静默期。
                            async with asyncio.timeout(_gen_timeout(format_list)):
                                _it = None
                                try:
                                    _it = llm.stream(
                                        prompt, image_urls=image_urls, cancel_event=cancel_event,
                                        on_reasoning=_reasoning_cb,
                                        reasoning_effort=reasoning_effort,
                                    )
                                    while True:
                                        try:
                                            chunk = await asyncio.wait_for(anext(_it), timeout=150)
                                        except StopAsyncIteration:
                                            break
                                        chunks.append(chunk)
                                        await _queue.put({"mode": label, "text": chunk})
                                finally:
                                    # P1-6：关闭流式生成器（正常/超时/异常路径均执行，防 httpx 连接泄漏）
                                    if _it is not None:
                                        try:
                                            await _it.aclose()
                                        except Exception:
                                            pass
                            if chunks:
                                break  # 成功
                            # 空响应（LLM finish=stop 但 0 chars）→ 重试
                            log.warning(f"{label} 空响应（第 {attempt + 1} 次），重试")
                        except asyncio.TimeoutError:
                            err_msg = f"{label} 生成超时（150s 无响应），已中断"
                            if not chunks and attempt == 0:
                                # 首次挂起（chunk 未到就断流）→ 重试一次
                                log.warning(f"{label} 挂起（第 {attempt + 1} 次），重试")
                                err_msg = None
                            else:
                                break
                        except CancelledError:
                            cancelled = True
                            break
                        except Exception as e:
                            err_msg = str(e)[:200]
                            if not chunks:
                                # 连接中断且无内容（deepseek 流式不稳定）→ 重试
                                log.warning(f"{label} 连接中断（第 {attempt + 1} 次）: {err_msg}")
                                err_msg = None
                            else:
                                break  # 已有部分内容，不重试
                    if cancelled:
                        await _queue.put({"mode": label, "type": "cancelled", "saved_partial": bool(chunks)})
                        return chunks, True, None
                    if err_msg:
                        # P0-9：per-product 失败改发 warning——前端 onWarning 只标 partial + toast，
                        # 不清双产物 buffer（原 error 事件 → onError 清空两路 + failTask，一半已保存被误报整体失败）
                        await _queue.put({"mode": label, "type": "warning",
                                          "message": f"{label} 生成失败: {err_msg}"})
                        return chunks, False, err_msg
                    await _queue.put({"mode": label, "type": "done"})
                    return chunks, False, None

                _tasks = [
                    asyncio.create_task(_run_product("notes", notes_prompt)),
                    # 整理版原文是忠实整理任务，无需深度思考——effort=low 大幅缩短
                    # deepseek-v4 思考静默期（默认 high：复杂任务思考 90s+ 零数据）
                    asyncio.create_task(_run_product("polished", polished_prompt, reasoning_effort="low")),
                ]
                # 汇合两路事件流（done/warning/cancelled 各 1 个后退出）。
                # warning 是 per-product 失败终态（P0-9：原 error 误触发前端 onError 清空双产物 buffer）
                _remaining = 2
                while _remaining:
                    _evt = await _queue.get()
                    if _evt.get("type") in ("done", "warning", "cancelled"):
                        _remaining -= 1
                    yield f"data: {json.dumps(_evt, ensure_ascii=False)}\n\n"
                # gather 返回顺序 = 任务创建顺序（_tasks[0]=notes, _tasks[1]=polished）。
                # 解包必须同序，否则 notes/polished 内容互换（此前解包反了 → 产物写反）
                (notes_chunks, notes_cancelled, notes_error), \
                    (polished_chunks, polished_cancelled, polished_error) = await asyncio.gather(*_tasks)
                was_cancelled = polished_cancelled or notes_cancelled

                # 保存双产物（取消时 status=partial，完成时 status=complete）
                status = "partial" if was_cancelled else "complete"
                if polished_chunks:
                    # P1-1：后处理（截图 ffmpeg + 图表 resvg）放线程池，释放事件循环
                    polished_content = await asyncio.to_thread(
                        _post_process,
                        "".join(polished_chunks), format_list,
                        video_path or screenshot_video_path, source_type, str(store._get_doc_dir(transcript_id) or ""),
                        render_chart=False)  # 图表只入 notes（P0-3）
                    mv = MarkdownView(
                        id=f"md_{uuid.uuid4().hex[:12]}",
                        transcript_id=transcript_id, content=polished_content,
                        mode=GenerateMode.POLISHED, style=Style(style), status=status,
                    )
                    store.save_markdown(mv)
                    _auto_title(store, transcript_id, mv.content)
                if notes_chunks:
                    # P1-1：后处理（截图 ffmpeg + 图表 resvg）放线程池，释放事件循环
                    raw_notes = "".join(notes_chunks)
                    notes_content = await asyncio.to_thread(
                        _post_process, raw_notes, format_list,
                        video_path or screenshot_video_path, source_type, str(store._get_doc_dir(transcript_id) or ""))
                    mv = MarkdownView(
                        id=f"md_{uuid.uuid4().hex[:12]}",
                        transcript_id=transcript_id, content=notes_content,
                        mode=GenerateMode.NOTES, style=Style(style), status=status,
                    )
                    store.save_markdown(mv)
                    _auto_title(store, transcript_id, mv.content)
                    # 图表未生成兜底（P1-3）：勾选图表但 LLM 未输出图表围栏
                    # （旧习惯 svg 围栏 / 裸内联 / 没画）→ warning 提示重新生成
                    if "chart" in format_list and not _has_chart_fence(raw_notes):
                        _judged_unsuitable = "图表说明" in raw_notes or "不适合图表化" in raw_notes
                        _reason = "模型判断内容不适合图表化" if _judged_unsuitable else "LLM 未输出图表围栏"
                        log.warning("图表未生成", source_type=source_type, reason=_reason)
                        yield f"data: {json.dumps({'type': 'warning', 'kind': 'chart_missing', 'message': f'图表未生成：{_reason}，请重新生成'}, ensure_ascii=False)}\n\n"

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
                # 重试后仍为空的产物（非取消）→ warning，前端 Toast 提示
                empty_products = [
                    p for p, chunks in (("polished", polished_chunks), ("notes", notes_chunks))
                    if not chunks and not was_cancelled
                ]

                if both_failed and not was_cancelled:
                    yield f"data: {json.dumps({'type': 'error', 'message': '生成失败：笔记与整理版均未成功，请重试'}, ensure_ascii=False)}\n\n"
                elif empty_products and not was_cancelled:
                    for p in empty_products:
                        yield f"data: {json.dumps({'type': 'warning', 'mode': p, 'message': f'{p} 生成结果为空（已重试一次）'}, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                elif one_failed and not was_cancelled:
                    # P0-9：单路失败详情已由 per-product warning 推送（前端 onWarning 标 partial + toast），
                    # 这里只发 done 收尾——避免重复 warning toast；onDone 据 genOutcomeRef 报「部分产物失败」
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
                processed_text = await ocr_engine.process(epub_text, str(store.root), doc_dir=str(store._get_doc_dir(transcript_id) or ""))

            clean_text, image_urls, video_path = await preparer.prepare(
                processed_text, vl_config, keep_refs, send_to_llm=send_to_llm,
                optimize_images=image_optimize, video_direct=video_direct)

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
                    video_understanding=video_understanding,
                    reference_texts=ref_texts)

            if partial_content:
                prompt = _build_continue_prompt(prompt, partial_content)

            try:
                # 思考链收集：回调只累积，anext 返回后统一带出（思考 chunk 到达 → anext 返回）
                _pending_reasoning: list[str] = []

                async def _reasoning_cb(text: str):
                    _pending_reasoning.append(text)

                # 总超时按 formats 分档（同双产物分支：勾选图表时放宽）
                async with asyncio.timeout(_gen_timeout(format_list)):
                    _it = None
                    try:
                        _it = llm.stream(
                            prompt, image_urls=image_urls, cancel_event=cancel_event,
                            on_reasoning=_reasoning_cb,
                            # 整理版原文：忠实整理任务，思考强度 low（缩短 deepseek 思考静默期）
                            reasoning_effort="low" if stream_mode == "polished" else None,
                        )
                        while True:
                            try:
                                chunk = await asyncio.wait_for(anext(_it), timeout=150)
                            except StopAsyncIteration:
                                break
                            if _pending_reasoning:
                                yield f"data: {json.dumps({'type': 'reasoning', 'text': ''.join(_pending_reasoning)}, ensure_ascii=False)}\n\n"
                                _pending_reasoning.clear()
                            all_chunks.append(chunk)
                            yield f"data: {json.dumps({'text': chunk}, ensure_ascii=False)}\n\n"
                    finally:
                        # P1-6：关闭流式生成器（正常/超时/异常路径均执行，防 httpx 连接泄漏）
                        if _it is not None:
                            try:
                                await _it.aclose()
                            except Exception:
                                pass
            except TimeoutError:
                # LLM 流式调用挂起（150s 无新 chunk）→ 强制结束，避免前端 isStreaming 卡死。
                # （与双产物路径的 chunk 间隔判定一致——此前 async for 断流要等满 330s）
                log.error("单产物生成超时（150s 无 chunk），强制结束", transcript_id=transcript_id)
                if all_chunks:
                    # P1-1：后处理放线程池，释放事件循环
                    content = await asyncio.to_thread(
                        _post_process, "".join(all_chunks), format_list,
                        video_path or screenshot_video_path, source_type, str(store._get_doc_dir(transcript_id) or ""),
                        render_chart=stream_mode != "polished")  # 图表只入 notes（P0-3）
                    mv_mode = GenerateMode.POLISHED if stream_mode == "polished" else GenerateMode.NOTES
                    mv = MarkdownView(
                        id=f"md_{uuid.uuid4().hex[:12]}",
                        transcript_id=transcript_id, content=content,
                        mode=mv_mode, style=Style(style), status="partial",
                    )
                    store.save_markdown(mv)
                    yield f"data: {json.dumps({'type': 'cancelled', 'saved_partial': True, 'id': mv.id}, ensure_ascii=False)}\n\n"
                else:
                    yield f"data: {json.dumps({'type': 'error', 'message': '生成超时（45s 无响应），请重试'}, ensure_ascii=False)}\n\n"
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
                return

            # 完成后保存（P1-1：后处理放线程池，释放事件循环）
            raw_content = "".join(all_chunks)
            content = await asyncio.to_thread(
                _post_process, raw_content, format_list,
                video_path or screenshot_video_path, source_type, str(store._get_doc_dir(transcript_id) or ""),
                render_chart=stream_mode != "polished")  # 图表只入 notes（P0-3）
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
            # 图表未生成兜底（P1-3）：勾选图表但 LLM 未输出图表围栏 → warning
            if "chart" in format_list and stream_mode != "polished" and not _has_chart_fence(raw_content):
                _judged_unsuitable = "图表说明" in raw_content or "不适合图表化" in raw_content
                _reason = "模型判断内容不适合图表化" if _judged_unsuitable else "LLM 未输出图表围栏"
                log.warning("图表未生成", source_type=source_type, reason=_reason)
                yield f"data: {json.dumps({'type': 'warning', 'kind': 'chart_missing', 'message': f'图表未生成：{_reason}，请重新生成'}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'id': mv.id}, ensure_ascii=False)}\n\n"

            # 视频清理统一在 finally（P0-5）：正常/取消/异常路径都删，防磁盘泄漏

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
            # P0-5：统一清理下载的视频文件（正常/取消/异常路径均执行，防每次取消泄漏 ~500MB）
            for _vp in (video_path, screenshot_video_path):
                if _vp and os.path.exists(_vp):
                    try:
                        os.remove(_vp)
                    except OSError:
                        pass
                    # P1-11：视频文件删后清空 VL 下载临时目录（vl_video_*），防目录泄漏。
                    # rmdir 只删空目录，非空（如残留）则跳过，安全。
                    _parent = os.path.dirname(_vp)
                    if os.path.basename(_parent).startswith("vl_video_"):
                        try:
                            os.rmdir(_parent)
                        except OSError:
                            pass
            # 清理断连监听
            _disconnect_task.cancel()
            try:
                await _disconnect_task
            except (asyncio.CancelledError, Exception):
                pass
            # 生成结束（完成/取消/超时/客户端断开）→ 删除未完成标记
            if marker_path and marker_path.exists():
                try:
                    marker_path.unlink()
                except OSError:
                    pass
            _active_generations.discard(transcript_id)
            # P1-8：断连/异常时取消双产物协程并收拢——否则孤儿任务继续烧 token
            # 直到流自然结束（结果被丢弃，资源空耗）。正常完成时 tasks 已 done，cancel 无害。
            for _t in _tasks:
                if not _t.done():
                    _t.cancel()
            if _tasks:
                try:
                    await asyncio.gather(*_tasks, return_exceptions=True)
                except Exception:
                    pass
            # 关闭 LLM 连接池（每请求一 LLMService，防 httpx client 常驻累积）
            try:
                await llm.aclose()
            except Exception:
                pass
            # P1-12：信号量释放绝不能被异常/二次取消逃逸——否则槽位永久丢失
            #（此前 release 在 finally 末尾，finally 内 await 若被取消会跳过它）
            try:
                guard.semaphore.release()
            except Exception:
                pass

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


def _normalize_generated_images(content: str, doc_dir: str) -> str:
    """规范化生成产物图片引用为相对 assets/ 路径（存储规范）。

    LLM 生成时可能把转录里的 assets/img_xxx.png 幻觉改写成 /api/files/... 或
    /api/assets/{tx_id}/... 绝对 URL（如 DSpark 笔记出现 /api/files/assets/img_0003.png
    → 前端 404）。存储需统一为相对 assets/xxx（文件在 {doc_dir}/assets/ 下）；
    data URI / 外部 http / 已是 assets/ 的相对路径保留。
    """
    if not doc_dir:
        return content

    def _repl(m):
        alt, url = m.group(1), m.group(2)
        if url.startswith('data:') or url.startswith('http') or url.startswith('assets/'):
            return m.group(0)
        # 绝对 API 路径（/api/files/... / /api/assets/{tx}/...）→ 提取文件名，校验在本文档 assets/ 下
        fname = url.rsplit('/', 1)[-1]
        if fname and (Path(doc_dir) / 'assets' / fname).exists():
            return f'![{alt}](assets/{fname})'
        return m.group(0)  # 无法确认 → 保留原文，不破坏

    return re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', _repl, content)


def _post_process(content: str, formats: list[str],
                  video_path, source_type: str, doc_dir: str = "",
                  render_chart: bool = True) -> str:
    """后处理（对齐原有 Generator 行为）

    render_chart=False 用于 polished——图表只入 notes，双产物同调渲染会写
    同名 chart_N.png 互相覆盖（P0-3：counter 每次调用重置）。
    """
    content = _post_process_content_markers(content)
    if "screenshot" in formats and video_path:
        content = _post_process_screenshots(content, str(video_path), doc_dir=doc_dir)
    if "chart" in formats and render_chart:
        content = render_charts(content, doc_dir)
    # LLM 可能把 assets/ 图片幻觉改写成 /api/... 绝对 URL → 规范化回相对 assets/（存储规范）
    content = _normalize_generated_images(content, doc_dir)
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

    # 按模式精确筛选（notes / polished 各自独立，不混在一起）
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

    # 改写磁盘相对路径 assets/xxx → 绝对 API URL
    result = {
        **selected,
        "versions": [{"version": m.get("version", 1), "id": m.get("id"),
                       "createdAt": m.get("createdAt"), "status": m.get("status", "complete")}
                      for m in mode_mds],
    }
    if result.get("content"):
        result["content"] = rewrite_asset_urls(result["content"], transcript_id)
    return result


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
        log.info(f"自动标题: {old_title[:30]} → {new_title}")
