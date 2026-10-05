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
from app.core.dependencies import get_store, get_config, get_guard, get_query_service
from app.core.errors import GenerateError, AppError
from app.core.reference_store import get_references
from app.core import logging as log
from app.core.model_resolver import resolve_llm_config, resolve_context_window
from app.llm import LLMService, CancelledError
from app.generation.generator import Generator, _post_process_content_markers, _post_process_screenshots
from app.generation.effort import effort_for, chunk_timeout_for
from app.generation.provenance import markdown_meta
from app.services.chart_renderer import render_charts, CHART_FENCE_MARKS
from app.services.doc_understand import DOC_DIRECT_GEN_TIMEOUT, DOC_DIRECT_MIN_RAW_CHARS
from app.generation.content_preparer import ContentPreparer
from app.generation.prompt_builder import PromptBuilder
from app.generation.prompt import build_polish_prompt, build_notes_prompt
from app.generation.prompts import coerce_style
from app.generation.reference_compressor import (
    needs_compression, compress_references_if_needed,
)
from app.storage.store import rewrite_asset_urls

router = APIRouter()


def _normalize_and_validate_style(style: str) -> str:
    """风格档入参（HTTP 侧）：**先归一、再校验**，非法值落默认档 `plain`。

    ⚠️ 判据已收敛到 `generation/prompts/notes_styles.py::coerce_style`（P1-05）：
    这段逻辑此前在本函数与 `generation/generator.py::_coerce_style` 各有一份，
    且**回退值不同**（`plain` vs `detailed`）——同一个非法输入，走流式与非流式
    两条路得到不同风格，而两侧都不报错。两条历史教训（归一必须排在校验之前 /
    合法集必须与下游能消费的集同源）随实现一并搬进了那边的 docstring。

    ⚠️ 保留这个壳（而不是让调用方直接调 `coerce_style`）：`tests/test_prompt.py`
    §8.4 直调本函数——那是 2026-10-05 特意抽出来的，为的是让测试**测真实实现**
    而不是复刻一遍表达式（模式 #51）。
    """
    return coerce_style(style)


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
# 定值依据（2026-09-19 实测，deepseek-flash，约 900 字文档）：
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


def _gen_timeout(formats, doc_direct: bool = False) -> float:
    """按 formats 选总超时。format 可传 list 或逗号分隔字符串。

    doc_direct=True（PDF 直传）：总超时还要把「模型解析原件的首包静默」算进去
    （≈10s/MB，见 doc_understand），否则首包一长就整段被掐断。
    """
    if isinstance(formats, str):
        fmts = [f.strip() for f in formats.split(",") if f.strip()]
    else:
        fmts = list(formats or [])
    base = _GEN_TIMEOUT_CHART if "chart" in fmts else _GEN_TIMEOUT_BASE
    if doc_direct:
        # 图表 + 直传同时开时取较大者（两者叠加不会同时达到各自峰值）
        return max(base, DOC_DIRECT_GEN_TIMEOUT)
    return base


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
    style: str = "plain"
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
    # 文档直传（source_type=document/PDF 专属）：原件随生成请求发给模型，
    # 本地抽取的正文**不进 prompt**（见 services/doc_understand.py 的模块说明）。
    doc_direct: bool = False
    # OCR 预处理（source_type=document 专属，与 image_analyze 互斥）
    ocr_enabled: bool = False
    ocr_model_id: int | None = None
    # 思考强度：""（未指定，走服务端默认）/ "low" / "high" / "max"。
    # ⚠️ 跨类型字段，**不进 _TYPE_FIELDS**（同 model_id / user_notes）。
    # 实际下发值由 generation/effort.py::effort_for 决定，不在此处判档。
    reasoning_effort: str = ""


def _resolve_reference_texts(ids: list[str]) -> list[dict]:
    """参考文件暂存 id → [{name, content}]（TTL 内有效；失效/不存在的 id 跳过，不注入）。"""
    return get_references([r for r in ids if r])


def _doc_direct_raw_warning(document: dict | None, raw_text: str) -> str | None:
    """直传成功但本地抽取文本近乎为空 → 返回提示文案，否则 None。

    这是**双真相源**唯一可观测的出口：模型读原件读到了内容，而转录 tab /
    导出 / FTS 搜索用的是本地 rawText（扫描件 + 无 OCR 时近乎空）。不提示的话
    「生成成功」会被当成「导出/搜索里也有」，直到导出时才发现是空的。

    ⚠️ 不是错误：产物本身完全成功，故调用方用 kind 走 info 提示而非 error。
    """
    if not document:
        return None
    chars = len((raw_text or "").strip())
    if chars >= DOC_DIRECT_MIN_RAW_CHARS:
        return None
    log.warning("文档直传: 本地抽取文本过短，转录/导出/搜索可能不完整",
                raw_chars=chars, filename=document.get("filename", ""))
    return ("本地未能从该文件抽取文本（直传模式下模型仍读到了原文件内容），"
            "转录/导出/搜索可能不完整")


@router.post("/generate/{transcript_id}")
async def generate(transcript_id: str, req: GenerateRequest, request: Request) -> dict[str, object]:
    """生成 Markdown — 结构化笔记 + 可选的整理版原文"""
    guard = request.app.state.guard
    async with guard.semaphore:
        store = request.app.state.store
        config = request.app.state.config
        llm_config = resolve_llm_config(config, req.model_id)
        # ── 上下文窗口：**不要再在这里手工赋值** ──
        # `resolve_llm_config` 内部已填 `model_context_window`（完整四层解析，见
        # model_resolver.py）。此处若再补一句 `resolve_context_window(config, req.model_id)`，
        # 传的是**原始** req.model_id —— 用户用「场景默认模型」时它是 None，会把上面
        # 已填好的正确值**覆盖成兜底值**（同一请求里模型 1M、预算却按兜底算）。
        # 收口在唯一入口，避免成对逻辑两处各写一遍而漂移（CLAUDE.md #39）。
        # ── LLMService ──
        # 非流式路径没有 asyncio.timeout 包壳，SDK 的 httpx 超时是唯一闸门，
        # 所以多图场景必须在这里就放宽，否则照样撞 330。
        llm = LLMService(config, model_id=req.model_id,
                         request_timeout=_gen_timeout(req.formats,
                                                      doc_direct=req.doc_direct))

        tx = store.get_transcript(transcript_id)
        if not tx:
            raise AppError("GEN_NOT_FOUND", "文档不存在，请先导入内容")
        if not llm_config.get("api_key"):
            raise AppError("GEN_NO_API_KEY", "LLM API Key 未配置，请在设置中填写", recoverable=False)

        # 文档直传的触发源（同流式端点的预检）：路径已是绝对路径（模式 #24）
        doc_path = tx.get("sourceFilePath", "") if req.doc_direct else ""
        if req.doc_direct and doc_path and not os.path.isfile(doc_path):
            log.warning("文档直传: 原件已不存在，改用文本模式", path=doc_path)
            doc_path = ""
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

        # ── 合成文档闸门（设计 §14.6，与流式端点同源）──
        # 只有一个产物：对比综述（落 notes 槽）。它的 rawText 已是模型输入的最终形态
        # （按 multi 预算恰好装满，§4.4.4 单真相源），polished 与任何格式处理都只会
        # 在装配阶段改动它。改写 mode 的**源头**而非事后覆盖 want_*：
        # 下游 `if want_notes and want_polished` / `elif want_polished` / `elif want_notes`
        # 三分支全由它推导，改一处比打三个补丁清楚。
        _is_composite = source_type == "composite"
        # ⚠️ 上面 :198 的 `request_timeout` 仍按 `req.formats` 原样算，不随此处清空——
        # 多留余量无害（与流式端点「闸门没过时多留余量无害」是同一取舍），
        # 且非流式路径没有 asyncio 总超时，收紧它只会让长输入更容易撞死。
        formats_list = [] if _is_composite else req.formats

        # ── 解析 mode 为产物列表（逗号分隔）──
        _raw_mode = "notes" if _is_composite else (req.mode if req.mode else "")
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
            preparer = ContentPreparer(
                str(store.root), llm_config,
                doc_dir=str(store.get_doc_dir(transcript_id) or ""),
                # 合成文档的 rawText 是按 multi 预算**恰好装满**的，生成侧必须同 profile
                # 同 cw —— 否则刚建好的综述会在生成时被二次截断（设计 §4.4.5 / §14.6）
                budget_profile="multi" if source_type == "composite" else "single",
            )
            prompt_builder = PromptBuilder(target_language=config.get("target_language", "zh-CN"))
            gen = Generator(llm, preparer, prompt_builder, config=config)

            # ── 截图用视频下载（非流式端点也需要，对标流式端点 line 376-396）──
            screenshot_video_path: str | None = None
            if "screenshot" in formats_list and source_type == "video" and not req.video_understanding:
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
                    source_type=tx.get("sourceType", "text"), formats=formats_list,
                    vl_config=vl_config, user_notes=req.user_notes,
                    reference_texts=_resolve_reference_texts(req.reference_ids),
                    image_analyze=req.image_analyze, image_optimize=req.image_optimize,
                    ocr_enabled=req.ocr_enabled, ocr_model_id=req.ocr_model_id,
                    video_direct=req.video_direct,
                    doc_direct=req.doc_direct, doc_path=doc_path,
                    reasoning_effort=req.reasoning_effort,
                )
                # 非流式端点截图后处理（generator 内部 video_path 可能为 None，此处补处理）
                if screenshot_video_path:
                    doc_dir = str(store.get_doc_dir(transcript_id) or "")
                    polished_mv.content = _post_process_screenshots(
                        polished_mv.content, screenshot_video_path, doc_dir=doc_dir)
                    notes_mv.content = _post_process_screenshots(
                        notes_mv.content, screenshot_video_path, doc_dir=doc_dir)
                polished_mv.transcript_id = transcript_id
                notes_mv.transcript_id = transcript_id
                store.save_markdown(polished_mv, file_meta=markdown_meta(
                    polished_mv, tx, formats=formats_list,
                    reasoning_effort=req.reasoning_effort))
                _auto_title(store, transcript_id, polished_mv.content)
                store.save_markdown(notes_mv, file_meta=markdown_meta(
                    notes_mv, tx, formats=formats_list,
                    reasoning_effort=req.reasoning_effort))
                log.info("Generate both done", tx_id=transcript_id,
                         polished_chars=len(polished_mv.content), notes_chars=len(notes_mv.content))
            elif want_polished:
                polished_mv = await gen.generate(
                    raw_text=epub_text, title=tx.get("title", ""), style=req.style,
                    source_type=tx.get("sourceType", "text"), mode="polished",
                    formats=formats_list, vl_config=vl_config, user_notes=req.user_notes,
                    image_analyze=req.image_analyze, image_optimize=req.image_optimize,
                    ocr_enabled=req.ocr_enabled, ocr_model_id=req.ocr_model_id,
                    video_direct=req.video_direct,
                    doc_direct=req.doc_direct, doc_path=doc_path,
                    reasoning_effort=req.reasoning_effort,
                )
                if screenshot_video_path:
                    polished_mv.content = _post_process_screenshots(
                        polished_mv.content, screenshot_video_path,
                        doc_dir=str(store.get_doc_dir(transcript_id) or ""))
                polished_mv.transcript_id = transcript_id
                store.save_markdown(polished_mv, file_meta=markdown_meta(
                    polished_mv, tx, formats=formats_list,
                    reasoning_effort=req.reasoning_effort))
                _auto_title(store, transcript_id, polished_mv.content)
                log.info("Generate done", tx_id=transcript_id, mode="polished", chars=len(polished_mv.content))
            elif want_notes:
                notes_mv = await gen.generate(
                    raw_text=epub_text, title=tx.get("title", ""), style=req.style,
                    source_type=tx.get("sourceType", "text"), mode="notes",
                    formats=formats_list, vl_config=vl_config,
                    user_notes=req.user_notes,
                    reference_texts=_resolve_reference_texts(req.reference_ids),
                    image_analyze=req.image_analyze, image_optimize=req.image_optimize,
                    ocr_enabled=req.ocr_enabled, ocr_model_id=req.ocr_model_id,
                    video_direct=req.video_direct,
                    doc_direct=req.doc_direct, doc_path=doc_path,
                    reasoning_effort=req.reasoning_effort,
                )
                if screenshot_video_path:
                    notes_mv.content = _post_process_screenshots(
                        notes_mv.content, screenshot_video_path,
                        doc_dir=str(store.get_doc_dir(transcript_id) or ""))
                notes_mv.transcript_id = transcript_id
                store.save_markdown(notes_mv, file_meta=markdown_meta(
                    notes_mv, tx, formats=formats_list,
                    reasoning_effort=req.reasoning_effort))
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
    style: str = "plain"
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
                          mode: str = "both", style: str = "plain",
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
                          doc_direct: bool = False,
                          ocr_enabled: bool = False,
                          ocr_model_id: int | None = None,
                          reasoning_effort: str = "",
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

    # 文档直传的触发源：本地原件路径。文档没有 sourceUrl，只能用它。
    # ⚠️ 该路径在 ingest 落盘时已是绝对路径（基于 store.root 拼接，模式 #24），
    # 这里只做存在性预检——`store.root/tmp/` 被外部清理时**静默降级比报错合理**，
    # 但必须留痕，否则「开了开关没反应」无从排查。
    doc_path = tx.get("sourceFilePath", "") if doc_direct else ""
    if doc_direct:
        if not doc_path:
            log.warning("文档直传: 该文档无本地原件路径，改用文本模式",
                        transcript_id=transcript_id, source_type=tx.get("sourceType", ""))
        elif not os.path.isfile(doc_path):
            log.warning("文档直传: 原件已不存在，改用文本模式", path=doc_path)
            doc_path = ""

    style = _normalize_and_validate_style(style)

    # ── source_type 上移到 mode 解析**之前**（合成文档闸门要按它改写 mode）──
    source_type = tx.get("sourceType", "text")

    # ── 解析 mode 为产物列表（逗号分隔）──
    _raw_mode = mode if mode else ""
    # ── 合成文档闸门（设计 §14.6）──
    # 合成文档**只有一个产物**：对比综述（落 notes 槽）。理由不是"没人给它做 polished"，
    # 而是它的 rawText 已经是模型输入的最终形态（按 multi 预算恰好装满，§4.4.4 单真相源）——
    # polished 与任何格式处理都只会在**装配阶段改动它**，把一个刻意定稿的输入变成第二个版本。
    # 在这里改写 `_raw_mode` 而不是事后覆盖 `stream_mode`：后面 want_notes / want_polished /
    # stream_mode 三处全由 mode_list 自然推导，改一处源头比在下游打三个补丁清楚。
    if source_type == "composite":
        _raw_mode = "notes"
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
    # 合成文档不支持任何格式处理（图表只入 notes、截图要视频、目录/翻译会改写正文）。
    # 清在这里而非 prompt 层：`format_list` 同时驱动 request_timeout 与 format 注入，
    # 只清一处会让「超时按图表档放宽、实际不生成图表」这种对不上的状态存在。
    if source_type == "composite":
        format_list = []
    # ⚠️ doc_direct 必须传（此前漏了，非流式那侧是传的）：request_timeout 是 httpx 的
    # read timeout，必须 ≥ 单 chunk 静默超时，否则直传时 SDK 会先于我们的 wait_for
    # 抛超时——异常被下游 except Exception 归到「连接中断」并按那条路重试，
    # 文案与真相不符（真正的死因是「模型还在解析原件，我们等得不够久」）。
    # 判据用 query 的**用户意图**而非 document 是否构造成功：后者在 prepare() 里才定，
    # 此刻还拿不到；且闸门没过时多留的余量无害（放宽只影响最坏等待，不影响正常路径）。
    llm = LLMService(config, model_id=model_id,
                     request_timeout=_gen_timeout(format_list, doc_direct=doc_direct))
    if not llm.api_key:
        raise AppError("GEN_NO_API_KEY", "LLM API Key 未配置，请在设置中填写", recoverable=False)

    # ── 内容准备 + Prompt 构建 ──
    # source_type 已在上方（mode 解析前）取好：ContentPreparer 要按它决定预算
    # profile，而 profile 必须在构造时就定下来。
    target_language = config.get("target_language", "zh-CN")
    prep_cfg = {
        "provider": llm.provider, "api_key": llm.api_key,
        "model": {llm.provider: llm.model},
        "base_url": {llm.provider: llm.base_url},
        "capabilities": llm.capabilities,
        # 上下文窗口：四层解析（与非流式端点同源）。
        # ⚠️ prep_cfg 是**手工构造**的（从 llm 对象反取字段），不走 `resolve_llm_config`
        #    ——所以这一行不能省，漏了就落到 provider 兜底表，两个端点的预算不同源。
        # ⚠️ `model_id` 可能是 **None**（用户用「场景默认模型」）→ 靠 resolve_context_window
        #    的第 0 层 scene 回退补上。别改成传 `llm.model` 之类的替代写法，那会绕过回退。
        "model_context_window": resolve_context_window(config, model_id),
    }
    preparer = ContentPreparer(
        str(store.root), prep_cfg,
        doc_dir=str(store.get_doc_dir(transcript_id) or ""),
        # 同非流式端点：合成文档必须用 multi profile，否则生成时二次截断（§4.4.5）
        budget_profile="multi" if source_type == "composite" else "single",
    )
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
        _doc_dir = store.get_doc_dir(transcript_id)
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
            # ── 出处头损坏提示（设计 §4.2.3 / §14.6）──
            # 合成文档的第一行 meta 若解析失败，`get_transcript` 会把它标为
            # `metadataCorrupt` 并**不猜**来源。这里只是告知，**不阻断生成**：
            # 正文还在，综述照样能写；坏掉的是「来源清单」，而用户正会拿它去核对。
            # 用户不知道它不可信的后果，比生成失败严重——他会照着错的清单一篇篇核。
            if tx.get("metadataCorrupt"):
                yield f"data: {json.dumps({'type': 'warning', 'kind': 'metadata_corrupt', 'message': '本文档的来源信息已损坏，来源清单不可用于核对'}, ensure_ascii=False)}\n\n"

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
                    processed_text = await ocr_engine.process(epub_text, str(store.root), doc_dir=str(store.get_doc_dir(transcript_id) or ""))

                # 预处理（VL + 图片剥离 + 文档直传 + 截断）
                clean_text, image_urls, video_path, document = await preparer.prepare(
                    processed_text, vl_config, keep_refs, send_to_llm=send_to_llm,
                    optimize_images=image_optimize, video_direct=video_direct,
                    doc_direct=doc_direct, doc_path=doc_path)
                # 直传时模型要先解析原件才吐第一个 token（实测最长 ~150s，见
                # doc_understand），这段没有任何 chunk——用 stage 事件让用户看得见，
                # 否则最坏路径（直传 + 生成 ≈ 10 分钟）看起来就是卡死。
                if document:
                    yield f"data: {json.dumps({'type': 'stage', 'message': '正在直传 PDF 原件给模型解析，大文件可能需要 1~2 分钟…'}, ensure_ascii=False)}\n\n"

                # 参考文件「原文 + 参考」超出模型上下文预算时先 LLM 摘要（装得下则原样
                # 返回，零成本）。不压缩的话整份塞进 prompt → 请求直接超窗失败。
                # 压缩可能耗时数十秒，先推 stage 事件，避免前端静默等待被误认为卡死。
                # ⚠️ 必须落到局部名 refs，不可写回外层的 ref_texts：merged_stream 是
                # 嵌套函数，内层一旦对它赋值，Python 就把 ref_texts 整段判定为内层
                # 局部变量 —— 紧随其后的读取直接 UnboundLocalError（2026-09-23 首次
                # 流式生成即炸）。压缩结果只给 notes 用，本就没有回传外层的必要。
                refs = ref_texts
                if refs and needs_compression(refs, clean_text, llm, config):
                    yield f"data: {json.dumps({'type': 'stage', 'message': '参考文件超出模型上下文，正在压缩…'}, ensure_ascii=False)}\n\n"
                refs = await compress_references_if_needed(
                    refs, clean_text, llm, config,
                    title=tx.get("title", ""), cancel_event=cancel_event)

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
                    reference_texts=refs)

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

                async def _run_product(label: str, prompt: str, effort: str | None = None) -> tuple[list[str], bool, str | None]:
                    """单产物：最多 2 次尝试（首试 + 1 次重试），事件经 queue 汇合。
                    返回 (chunks, cancelled, error_msg)。

                    effort：**已决策好的**思考强度（None = 不传参数），由 effort_for
                    按产物名算出——本函数不认识档位表，也不该认识。"""
                    chunks: list[str] = []
                    err_msg: str | None = None
                    cancelled = False
                    # 静默期超时：按档位取（max 档思考更久）+ 直传时按原件解析耗时放宽
                    _dd = document is not None
                    chunk_timeout = chunk_timeout_for(effort, doc_direct=_dd)
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
                            # _GEN_TIMEOUT_CHART）+ chunk 间隔超时按 effort 分档（150/180）：
                            # DeepSeek 思考模式思考阶段服务端静默不推流（实测首 chunk 后
                            # 90s 零数据）——45s 判定会把正常长思考误判为挂起；推理链 chunk
                            # 到达时 anext 正常返回（有 reasoning 也在推流），不触发超时。
                            # 两个超时互相独立：总超时管整体，单 chunk 超时管静默期。
                            async with asyncio.timeout(_gen_timeout(format_list, doc_direct=_dd)):
                                _it = None
                                try:
                                    _it = llm.stream(
                                        prompt, image_urls=image_urls, document=document,
                                        cancel_event=cancel_event,
                                        on_reasoning=_reasoning_cb,
                                        reasoning_effort=effort,
                                    )
                                    while True:
                                        try:
                                            chunk = await asyncio.wait_for(anext(_it), timeout=chunk_timeout)
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
                            err_msg = f"{label} 生成超时（{int(chunk_timeout)}s 无响应），已中断"
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

                # ⚠️ effort 按**产物名**传，不按 a/b 位置传：本列表与下方解包同为
                # [notes, polished] 顺序，而 generator.generate_both 的 a/b 是反的
                # （a=polished）。位置一旦弄混，就是把笔记的档位配到整理版上。
                _tasks = [
                    asyncio.create_task(_run_product(
                        "notes", notes_prompt,
                        effort_for("notes", reasoning_effort, llm.supports_reasoning))),
                    # 整理版原文是忠实整理任务，无需深度思考——固定低档大幅缩短
                    # DeepSeek 思考静默期（默认 high：复杂任务思考 90s+ 零数据）
                    asyncio.create_task(_run_product(
                        "polished", polished_prompt,
                        effort_for("polished", reasoning_effort, llm.supports_reasoning))),
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
                    # P1-1：后处理（截图 ffmpeg + 图表渲染）放线程池，释放事件循环
                    polished_content = await asyncio.to_thread(
                        _post_process,
                        "".join(polished_chunks), format_list,
                        video_path or screenshot_video_path, source_type, str(store.get_doc_dir(transcript_id) or ""),
                        render_chart=False)  # 图表只入 notes（P0-3）
                    mv = MarkdownView(
                        id=f"md_{uuid.uuid4().hex[:12]}",
                        transcript_id=transcript_id, content=polished_content,
                        mode=GenerateMode.POLISHED, style=Style(style), status=status,
                        generation_model=llm.model,
                    )
                    store.save_markdown(mv, file_meta=markdown_meta(
                        mv, tx, formats=format_list, reasoning_effort=reasoning_effort))
                    _auto_title(store, transcript_id, mv.content)
                if notes_chunks:
                    # P1-1：后处理（截图 ffmpeg + 图表渲染）放线程池，释放事件循环
                    raw_notes = "".join(notes_chunks)
                    notes_content = await asyncio.to_thread(
                        _post_process, raw_notes, format_list,
                        video_path or screenshot_video_path, source_type, str(store.get_doc_dir(transcript_id) or ""))
                    mv = MarkdownView(
                        id=f"md_{uuid.uuid4().hex[:12]}",
                        transcript_id=transcript_id, content=notes_content,
                        mode=GenerateMode.NOTES, style=Style(style), status=status,
                        generation_model=llm.model,
                    )
                    store.save_markdown(mv, file_meta=markdown_meta(
                        mv, tx, formats=format_list, reasoning_effort=reasoning_effort))
                    _auto_title(store, transcript_id, mv.content)
                    # 图表未生成兜底（P1-3）：勾选图表但 LLM 未输出图表围栏
                    # （旧习惯 svg 围栏 / 裸内联 / 没画）→ warning 提示重新生成
                    if "chart" in format_list and not _has_chart_fence(raw_notes):
                        _judged_unsuitable = "图表说明" in raw_notes or "不适合图表化" in raw_notes
                        _reason = "模型判断内容不适合图表化" if _judged_unsuitable else "LLM 未输出图表围栏"
                        log.warning("图表未生成", source_type=source_type, reason=_reason)
                        yield f"data: {json.dumps({'type': 'warning', 'kind': 'chart_missing', 'message': f'图表未生成：{_reason}，请重新生成'}, ensure_ascii=False)}\n\n"

                # 直传成功但本地抽取近乎为空 → 显式提示（双真相源的可观测出口）
                _raw_warn = _doc_direct_raw_warning(document, epub_text)
                if _raw_warn:
                    yield f"data: {json.dumps({'type': 'warning', 'kind': 'doc_direct_raw_short', 'message': _raw_warn}, ensure_ascii=False)}\n\n"

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
                processed_text = await ocr_engine.process(epub_text, str(store.root), doc_dir=str(store.get_doc_dir(transcript_id) or ""))

            clean_text, image_urls, video_path, document = await preparer.prepare(
                processed_text, vl_config, keep_refs, send_to_llm=send_to_llm,
                optimize_images=image_optimize, video_direct=video_direct,
                doc_direct=doc_direct, doc_path=doc_path)
            if document:
                yield f"data: {json.dumps({'type': 'stage', 'message': '正在直传 PDF 原件给模型解析，大文件可能需要 1~2 分钟…'}, ensure_ascii=False)}\n\n"

            # 参考文件超出模型上下文预算时先摘要（同双产物分支）。
            # 参考文件只注入 notes —— 单产物 polished 时压缩纯属白花钱，直接跳过。
            # ⚠️ 同双产物分支：用局部名 refs，绝不写回外层 ref_texts（嵌套函数内赋值
            # 会让外层同名变量变成局部变量 → UnboundLocalError）。
            if stream_mode != "polished":
                refs = ref_texts
                if refs and needs_compression(refs, clean_text, llm, config):
                    yield f"data: {json.dumps({'type': 'stage', 'message': '参考文件超出模型上下文，正在压缩…'}, ensure_ascii=False)}\n\n"
                refs = await compress_references_if_needed(
                    refs, clean_text, llm, config,
                    title=tx.get("title", ""), cancel_event=cancel_event)

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
                    reference_texts=refs)

            if partial_content:
                prompt = _build_continue_prompt(prompt, partial_content)

            # 档位决策与双产物分支同源（effort.py）——单产物 mode 就是 product 名。
            # ⚠️ 必须放在 try **之外**：下面的 except TimeoutError 要拿 chunk_timeout
            # 拼超时文案，定义在 try 内的话异常早于赋值时该名未绑定（UnboundLocalError）。
            effort = effort_for(stream_mode, reasoning_effort, llm.supports_reasoning)
            _dd = document is not None
            chunk_timeout = chunk_timeout_for(effort, doc_direct=_dd)

            try:
                # 思考链收集：回调只累积，anext 返回后统一带出（思考 chunk 到达 → anext 返回）
                _pending_reasoning: list[str] = []

                async def _reasoning_cb(text: str):
                    _pending_reasoning.append(text)

                # 总超时按 formats 分档（同双产物分支：勾选图表时放宽）
                async with asyncio.timeout(_gen_timeout(format_list, doc_direct=_dd)):
                    _it = None
                    try:
                        _it = llm.stream(
                            prompt, image_urls=image_urls, document=document,
                            cancel_event=cancel_event,
                            on_reasoning=_reasoning_cb,
                            reasoning_effort=effort,
                        )
                        while True:
                            try:
                                chunk = await asyncio.wait_for(anext(_it), timeout=chunk_timeout)
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
                # LLM 流式调用挂起（chunk_timeout 秒无新 chunk）→ 强制结束，避免前端 isStreaming 卡死。
                # （与双产物路径的 chunk 间隔判定一致——此前 async for 断流要等满 330s）
                # ⚠️ 文案必须用 chunk_timeout（档位相关），写死数字会在 max 档下报错值
                log.error("单产物生成超时（chunk 静默超时），强制结束",
                          transcript_id=transcript_id, chunk_timeout=chunk_timeout,
                          effort=effort or "default")
                if all_chunks:
                    # P1-1：后处理放线程池，释放事件循环
                    content = await asyncio.to_thread(
                        _post_process, "".join(all_chunks), format_list,
                        video_path or screenshot_video_path, source_type, str(store.get_doc_dir(transcript_id) or ""),
                        render_chart=stream_mode != "polished")  # 图表只入 notes（P0-3）
                    mv_mode = GenerateMode.POLISHED if stream_mode == "polished" else GenerateMode.NOTES
                    mv = MarkdownView(
                        id=f"md_{uuid.uuid4().hex[:12]}",
                        transcript_id=transcript_id, content=content,
                        mode=mv_mode, style=Style(style), status="partial",
                        generation_model=llm.model,
                    )
                    store.save_markdown(mv, file_meta=markdown_meta(
                        mv, tx, formats=format_list, reasoning_effort=reasoning_effort))
                    yield f"data: {json.dumps({'type': 'cancelled', 'saved_partial': True, 'id': mv.id}, ensure_ascii=False)}\n\n"
                else:
                    yield f"data: {json.dumps({'type': 'error', 'message': f'生成超时（{int(chunk_timeout)}s 无响应），请重试'}, ensure_ascii=False)}\n\n"
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
                video_path or screenshot_video_path, source_type, str(store.get_doc_dir(transcript_id) or ""),
                render_chart=stream_mode != "polished")  # 图表只入 notes（P0-3）
            mv_mode = GenerateMode.POLISHED if stream_mode == "polished" else GenerateMode.NOTES
            mv = MarkdownView(
                id=f"md_{uuid.uuid4().hex[:12]}",
                transcript_id=transcript_id, content=content,
                mode=mv_mode, style=Style(style),
                generation_model=llm.model,
            )
            if partial_content:
                # 续写完成 → 合并内容保存，标记 complete
                mv.status = "complete"
                mv.content = partial_content.rstrip() + "\n\n" + content
            store.save_markdown(mv, file_meta=markdown_meta(
                mv, tx, formats=format_list, reasoning_effort=reasoning_effort))
            _auto_title(store, transcript_id, mv.content)
            # 图表未生成兜底（P1-3）：勾选图表但 LLM 未输出图表围栏 → warning
            if "chart" in format_list and stream_mode != "polished" and not _has_chart_fence(raw_content):
                _judged_unsuitable = "图表说明" in raw_content or "不适合图表化" in raw_content
                _reason = "模型判断内容不适合图表化" if _judged_unsuitable else "LLM 未输出图表围栏"
                log.warning("图表未生成", source_type=source_type, reason=_reason)
                yield f"data: {json.dumps({'type': 'warning', 'kind': 'chart_missing', 'message': f'图表未生成：{_reason}，请重新生成'}, ensure_ascii=False)}\n\n"
            # 直传成功但本地抽取近乎为空 → 显式提示（双真相源的可观测出口）
            _raw_warn = _doc_direct_raw_warning(document, epub_text)
            if _raw_warn:
                yield f"data: {json.dumps({'type': 'warning', 'kind': 'doc_direct_raw_short', 'message': _raw_warn}, ensure_ascii=False)}\n\n"
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
                    generation_model=llm.model,
                )
                store.save_markdown(mv, file_meta=markdown_meta(
                    mv, tx, formats=format_list, reasoning_effort=reasoning_effort))
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
async def get_markdown(transcript_id: str, mode: str = "", version: int = 0, request: Request = None, svc=Depends(get_query_service)) -> dict[str, object]:
    """获取指定 transcript 的生成结果。mode=polished 返回整理版原文，version=0 返回最新

    ⚠️ **一次读盘同时喂给「版本列表」与「选中内容」**：`get_markdowns_by_transcript`
    一次就把两者都读出来了，故经 `build_from_entries` 成形。分成两次调用（列表走
    store、内容走 `get_artifact`）会让两次读盘之间被生成任务替换的文件露出来 ——
    用户看到「版本列表里的第 2 版」配着「第 1 版的正文」。
    """
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

    # 选版本：默认最新（列表已按版本降序，[0] 即最新）。显式版本查不到**不回退最新**
    # ——「我要第 1 版」拿到第 2 版是最坏的一种成功。
    if version > 0:
        picked = [m for m in mode_mds if m.get("version") == version]
        if not picked:
            raise AppError("MD_NOT_FOUND", f"版本 {version} 不存在", status_code=404)
        mode_mds = picked

    art = svc.build_from_entries(transcript_id, mode_mds, max_chars=None)

    # 响应形状保持不变（前端 `MarkdownView` 依赖 id/style/mode/version/createdAt）：
    # Artifact 的 `entry` 带着原始 index 条目，正是为了这里能原样还原。
    # `content` 用 `raw_content`（**含自证头**）——源码编辑器应当能看到它，预览区
    # 不渲染 HTML 注释故不受影响。
    # 改写磁盘相对路径 assets/xxx → 绝对 API URL（HTTP 层职责）。
    result = {
        **art.entry,
        "content": rewrite_asset_urls(art.raw_content, transcript_id),
        "versions": [{"version": m.get("version", 1), "id": m.get("id"),
                      "createdAt": m.get("createdAt"), "status": m.get("status", "complete"),
                      "mode": m.get("mode", "")}
                     for m in ([md for md in mds if not mode or md.get("mode", "") == mode])],
    }
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
    # ── 合成文档跳过（设计 §14.6）──
    # 本函数的**前提**是「这个标题是机器凑出来的，可以覆盖」（URL / 哈希 / 未命名）。
    # 而合成文档的标题是用户在对话框里输入的合并名——属**用户意图**。
    # ⚠️ 不拦会被下面的 `is_auto` 判据误伤：合并名常含 `_`（多篇标题拼接）或超 40 字，
    # 正好落在「技术名」「超长文本」两条里 → 用户起的名字被生成正文首行顶掉。
    if tx.get("sourceType", "") == "composite":
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
