"""
Generator — LLM 生成编排器

职责：接收准备好的内容 → 构建 prompt → 调用 LLM → 后处理 → 返回 MarkdownView。
内容准备（VL/图片剥离/截断）已下沉到 ContentPreparer，
prompt 构建已下沉到 PromptBuilder。
"""

import asyncio
import logging
import os
import tempfile
import uuid
from pathlib import Path

from app.core.types import MarkdownView, Style, GenerateMode
from app.generation.effort import effort_for
from app.generation.prompts import coerce_style
from app.generation.reference_compressor import compress_references_if_needed
from app.llm.service import LLMService
from app.services.chart_renderer import render_charts


def _coerce_style(style: str) -> Style:
    """外部传入的 style 字符串 → `Style` 枚举。

    ⚠️ 判据已收敛到 `prompts.notes_styles.py::coerce_style`（P1-05）：本函数此前
    自带一份「不认识的值 → `DETAILED`」的兜底，与 HTTP 侧（→ `plain`）**不同源**
    —— 同一个非法输入，走流式与非流式两条路得到不同风格，而两侧都不报错。

    ⚠️ 那段「回退 `DETAILED` 而非新默认档 `PLAIN`，因为这是兜底不是默认值的第二处
    定义」的旧论证**已废弃**：兜底值与默认值不同，等于说「传一个不存在的档」比
    「不传」更严谨详细。理由详见 `coerce_style` 的 docstring。

    ⚠️ 那段逻辑此前在本文件里**逐字重复了 5 次**（且 5 处全都没有归一）——
    模式 #54：同一逻辑 ≥3 次即抽。

    `coerce_style` 的返回值由 `Style` 枚举的合法集派生，故这里的 `Style(...)`
    **不可能**抛 `ValueError`。
    """
    return Style(coerce_style(style))

logger = logging.getLogger(__name__)

MAX_TOKENS = 65536


# ── 后处理（模块级工具函数） ──

def _post_process_content_markers(markdown: str) -> str:
    """Content-[mm:ss] 标记由前端渲染为时间戳 badge，后端不做替换。
    仅移除可能残留的前导 *（避免被 markdown 解析为斜体）。"""
    import re
    return re.sub(r'\*(Content-\[\d{1,2}:\d{1,2}\])', r'\1', markdown)


def _screenshot_fail_note(ts: int) -> str:
    """截图失败提示文本（替换标记，零残留）。

    ⚠️ 与 `chart_renderer._fail_note` 同形态、同纪律：失败的**标记必然被替换**——
    成功的产物里有引用、失败的产物里有原因，永不留下内部标记（P1-07）。

    ⚠️ 不塞异常文本：ffmpeg 的报错是本机绝对路径 + 英文栈信息，而这段字符串要进
    **用户产物正文**（图表侧同理——它传的是中文校验原因，不是 `repr(e)`）。

    ⚠️ 用 `divmod` 重新格式化而不是把 `marker` 原样嵌进去：marker 的形态有四种
    （带不带括号、前导 `*` 有无），嵌进去会让失败提示**读起来千奇百怪**；失败提示
    应当形态一致。
    """
    mm, ss = divmod(max(ts, 0), 60)
    return f"\n📸 截图生成失败（{mm:02d}:{ss:02d}）\n"


def _post_process_screenshots(markdown: str, video_path: str | None, doc_dir: str = "") -> str:
    """将 *Screenshot-[mm:ss] 标记替换为 assets/xxx 相对路径截图引用"""
    if not video_path or not os.path.exists(video_path):
        logger.info("原片截图：无视频文件，跳过（video_path=%s）", video_path)
        return markdown
    if not doc_dir:
        # P1-9：无目标目录时跳过——写入随机 mkdtemp 会永久泄漏目录，且截图引用
        # （assets/xxx 相对路径）必然 404。宁可跳过替换也不写孤儿目录。
        logger.info("原片截图：无 doc_dir，跳过截图替换")
        return markdown

    try:
        from app.utils.screenshot_marker import extract_screenshot_timestamps
        from app.utils.video_helper import generate_screenshot

        matches = extract_screenshot_timestamps(markdown)
        if not matches:
            logger.info("原片截图：LLM 未生成 *Screenshot-[mm:ss] 标记")
            return markdown

        logger.info("原片截图：找到 %d 个标记，开始 ffmpeg 抽帧", len(matches))
        output_dir = os.path.join(doc_dir, "assets")
        os.makedirs(output_dir, exist_ok=True)
        for idx, (marker, ts) in enumerate(matches):
            try:
                img_path = generate_screenshot(video_path, output_dir, ts, idx)
                filename = Path(img_path).name
                img_url = f"assets/{filename}"
                markdown = markdown.replace(marker, f"![]({img_url})", 1)
                logger.info("原片截图：%s → %s", marker, filename)
            except Exception as e:
                # P1-07：失败**必须替换标记**（此前只 logger.warning，标记原样留在正文）。
                # ⚠️ 而前端把 `Screenshot-[mm:ss]` 渲染成橙色「📷 截图标」badge
                # （`markdown-components.tsx:237`）——于是失败的产物**看起来与成功的
                # 一模一样**，只是点了没反应。用户无从知道图没生成、更不知道要重跑。
                logger.warning("截图生成失败 ts=%ds: %s", ts, e)
                markdown = markdown.replace(marker, _screenshot_fail_note(ts), 1)

        return markdown
    except Exception as e:
        logger.warning("截图后处理失败: %s", e)
        return markdown


# ── Generator ──

class Generator:
    """LLM 生成编排器。依赖 ContentPreparer + PromptBuilder + LLMService。"""

    def __init__(
        self,
        llm_service: LLMService,
        content_preparer,   # ContentPreparer
        prompt_builder,     # PromptBuilder
        config: dict | None = None,
    ):
        self._llm = llm_service
        self._prep = content_preparer
        self._prompts = prompt_builder
        # OCRPreprocessor 需要完整 config（LLM Key / 引擎配置）— 之前未初始化导致 OCR 路径 AttributeError
        self.config = config or {}

    # ── 公开接口 ──

    async def generate(
        self,
        raw_text: str,
        title: str = "",
        style: str = "plain",
        source_type: str = "text",
        mode: str = "notes",
        formats: list[str] | None = None,
        vl_config: dict | None = None,
        user_notes: str = "",
        reference_texts: list[dict] | None = None,
        image_analyze: bool = False,
        image_optimize: bool = False,
        ocr_enabled: bool = False,
        ocr_model_id: int | None = None,
        video_direct: bool = False,
        doc_direct: bool = False,
        doc_path: str = "",
        reasoning_effort: str = "",
        cancel_event: asyncio.Event | None = None,
    ) -> MarkdownView:
        """生成单个产物（notes 或 polished）。

        reasoning_effort：用户档位（""/"low"/"high"/"max"），**原样透传**——
        实际下发值由 effort_for 按产物名算出（polished 恒低档），本方法不判档。
        doc_direct + doc_path：文档直传（PDF 原件随请求发给模型，本地正文不进 prompt）。
        """
        keep_refs = formats is not None and "images" in formats
        send_to_llm = bool(image_analyze)

        # OCR 预处理：图片→文字→塞回原文（prepare 之前，需要原始图片路径）
        processed_text = raw_text
        if ocr_enabled and ocr_model_id:
            from app.generation.ocr_preprocessor import OCRPreprocessor
            ocr = OCRPreprocessor(self.config, ocr_model_id)
            processed_text = await ocr.process(
                raw_text, self._prep.store_root, doc_dir=self._prep.doc_dir)

        clean_text, image_urls, video_path, document = await self._prep.prepare(
            processed_text, vl_config, keep_refs,
            send_to_llm=send_to_llm, optimize_images=image_optimize,
            video_direct=video_direct,
            doc_direct=doc_direct, doc_path=doc_path,
        )

        # 参考文件「原文 + 参考」超出模型上下文预算时先 LLM 摘要（装得下则原样返回，零成本）。
        # 参考文件只注入 notes —— polished 侧压缩纯属白花钱，直接跳过。
        if mode != "polished":
            reference_texts = await compress_references_if_needed(
                reference_texts, clean_text, self._llm, self.config,
                title=title, cancel_event=cancel_event)

        prompt = self._prompts.build(
            source_type, mode, clean_text, title, style, formats, user_notes,
            reference_texts=reference_texts,
        )

        logger.info("Generating %s...", mode)
        # 档位按产物名决策（mode 即 product：notes/polished），与流式路径同源
        effort = effort_for(mode, reasoning_effort, self._llm.supports_reasoning)
        try:
            content = await self._llm.call(
                prompt, image_urls=image_urls, document=document,
                max_tokens=MAX_TOKENS,
                temperature=0.7, max_continuations=3,
                cancel_event=cancel_event, reasoning_effort=effort,
            )
            content = _post_process_content_markers(content)
            if formats and "screenshot" in formats:
                # P1-1：ffmpeg 抽帧放线程池，释放事件循环
                content = await asyncio.to_thread(
                    _post_process_screenshots, content, video_path, doc_dir=self._prep.doc_dir)
            if formats and "chart" in formats:
                # P1-1：图表渲染放线程池，释放事件循环
                content = await asyncio.to_thread(render_charts, content, self._prep.doc_dir)
        finally:
            self._cleanup_video(video_path)

        return MarkdownView(
            id=f"md_{uuid.uuid4().hex[:12]}",
            transcript_id="",
            content=content,
            style=_coerce_style(style),
            mode=GenerateMode(mode) if mode in GenerateMode._value2member_map_ else GenerateMode.NOTES,
            # 自证头第②条「怎么来的」要记生成模型。它是**唯二**知道模型的地方
            # （另一个是 LLMService 自己），落盘侧（router）拿不到 —— 故在此就地填。
            # 此前该字段声明了却从无赋值（死字段），文档 §2「markdown 条目没有生成模型」
            # 说的就是它。
            generation_model=self._llm.model,
        )

    async def generate_both(
        self,
        raw_text: str,
        title: str = "",
        style: str = "plain",
        source_type: str = "text",
        formats: list[str] | None = None,
        vl_config: dict | None = None,
        user_notes: str = "",
        reference_texts: list[dict] | None = None,
        image_analyze: bool = False,
        image_optimize: bool = False,
        ocr_enabled: bool = False,
        ocr_model_id: int | None = None,
        video_direct: bool = False,
        doc_direct: bool = False,
        doc_path: str = "",
        reasoning_effort: str = "",
        cancel_event: asyncio.Event | None = None,
    ) -> tuple[MarkdownView, MarkdownView]:
        """并行生成双产物（polished + notes）。Excel 生成 report + summary。

        reasoning_effort：用户档位（""/"low"/"high"/"max"），原样透传，实际下发值见
        effort_for。⚠️ 本方法内 prompt_a 产出 **polished**、prompt_b 产出 **notes**
        （与流式路径的 notes→polished 顺序相反）——档位**按产物名传，不按 a/b 位置传**。
        """
        keep_refs = formats is not None and "images" in formats
        send_to_llm = bool(image_analyze)

        # OCR 预处理：图片→文字→塞回原文（prepare 之前，需要原始图片路径）
        processed_text = raw_text
        if ocr_enabled and ocr_model_id:
            from app.generation.ocr_preprocessor import OCRPreprocessor
            ocr = OCRPreprocessor(self.config, ocr_model_id)
            processed_text = await ocr.process(
                raw_text, self._prep.store_root, doc_dir=self._prep.doc_dir)

        clean_text, image_urls, video_path, document = await self._prep.prepare(
            processed_text, vl_config, keep_refs,
            send_to_llm=send_to_llm, optimize_images=image_optimize,
            video_direct=video_direct,
            doc_direct=doc_direct, doc_path=doc_path,
        )

        # 参考文件超出模型上下文预算时先摘要（同上）。双产物里 reference 只注入 notes，
        # 但此路径必然包含 notes，故无需 mode 判断。
        # ⚠️ xlsx 例外：build_both 对 Excel 走专属分支（只出数据摘要），**不注入
        # reference_texts**（实测确认），压缩纯属白花钱——省掉一次几十秒的调用。
        if source_type != "xlsx":
            reference_texts = await compress_references_if_needed(
                reference_texts, clean_text, self._llm, self.config,
                title=title, cancel_event=cancel_event)

        prompt_a, prompt_b = self._prompts.build_both(
            source_type, clean_text, title, style, formats, user_notes,
            reference_texts=reference_texts,
        )

        # Excel 只需一个数据摘要产物（分析报告已移除——LLM 无法做数值分析）
        if source_type == "xlsx":
            logger.info("Generating Excel summary...")
            # Excel 摘要二者同源，按 notes 取档（与流式路径对 xlsx 的处理一致）
            effort = effort_for("notes", reasoning_effort, self._llm.supports_reasoning)
            try:
                content = await self._llm.call(
                    prompt_a, image_urls=image_urls, document=document,
                    max_tokens=MAX_TOKENS, temperature=0.7,
                    max_continuations=3, cancel_event=cancel_event,
                    reasoning_effort=effort,
                )
                content = _post_process_content_markers(content)
            finally:
                self._cleanup_video(video_path)
            mv = MarkdownView(
                id=f"md_{uuid.uuid4().hex[:12]}", transcript_id="",
                content=content,
                style=_coerce_style(style),
                mode=GenerateMode.NOTES,
                generation_model=self._llm.model,
            )
            polished = MarkdownView(
                id=f"md_{uuid.uuid4().hex[:12]}", transcript_id="",
                content=content,
                style=_coerce_style(style),
                mode=GenerateMode.POLISHED,
                generation_model=self._llm.model,
            )
            return polished, mv

        logger.info("Generating polished + notes in parallel...")
        # ⚠️ 档位按**产物名**传，不按 a/b 位置传：本文件 prompt_a → polished、
        # prompt_b → notes（与流式路径 [notes, polished] 的顺序相反）。位置一旦弄混，
        # 就是把笔记的档位配到整理版上——而这里恰好有「曾解包反了导致两产物互换」的历史。
        _supports_reasoning = self._llm.supports_reasoning
        effort_a = effort_for("polished", reasoning_effort, _supports_reasoning)
        effort_b = effort_for("notes", reasoning_effort, _supports_reasoning)
        try:
            task_a = self._llm.call(
                prompt_a, image_urls=image_urls, document=document,
                max_tokens=MAX_TOKENS,
                temperature=0.7, max_continuations=3,
                cancel_event=cancel_event, reasoning_effort=effort_a,
            )
            task_b = self._llm.call(
                prompt_b, image_urls=image_urls, document=document,
                max_tokens=MAX_TOKENS, temperature=0.7,
                max_continuations=3, cancel_event=cancel_event,
                reasoning_effort=effort_b,
            )
            # P0-4：gather 默认遇单路异常立即传播 → 另一路成孤儿 task（烧 token 结果被丢弃 +
            # unhandled exception），finally 的 _cleanup_video 也可能在另一路运行时删视频。
            # return_exceptions 等两路全部完成（结果不孤立、不提前删视频），有异常则重抛首个。
            results = await asyncio.gather(task_a, task_b, return_exceptions=True)
            exc = next((r for r in results if isinstance(r, BaseException)), None)
            if exc is not None:
                raise exc
            content_a, content_b = results

            content_a = _post_process_content_markers(content_a)
            content_b = _post_process_content_markers(content_b)
            if formats and "screenshot" in formats:
                # P1-1：ffmpeg 抽帧放线程池（两路并行），释放事件循环
                content_a, content_b = await asyncio.gather(
                    asyncio.to_thread(_post_process_screenshots, content_a, video_path, doc_dir=self._prep.doc_dir),
                    asyncio.to_thread(_post_process_screenshots, content_b, video_path, doc_dir=self._prep.doc_dir),
                )
            if formats and "chart" in formats:
                # 图表只入 notes（content_b）——polished 也渲染会写同名 chart_N.png 覆盖 notes 图（P0-3）
                # P1-1：图表渲染放线程池，释放事件循环
                content_b = await asyncio.to_thread(render_charts, content_b, self._prep.doc_dir)
        finally:
            self._cleanup_video(video_path)

        polished = MarkdownView(
            id=f"md_{uuid.uuid4().hex[:12]}", transcript_id="",
            content=content_a,
            style=_coerce_style(style),
            mode=GenerateMode.POLISHED,
            generation_model=self._llm.model,
        )
        notes = MarkdownView(
            id=f"md_{uuid.uuid4().hex[:12]}", transcript_id="",
            content=content_b,
            style=_coerce_style(style),
            mode=GenerateMode.NOTES,
            generation_model=self._llm.model,
        )
        return polished, notes

    @staticmethod
    def _cleanup_video(video_path: str | None):
        if video_path and os.path.exists(video_path):
            try:
                os.remove(video_path)
            except OSError:
                pass
            # P1-11：删视频后清空 VL 下载临时目录（vl_video_*），防目录泄漏
            _parent = os.path.dirname(video_path)
            if os.path.basename(_parent).startswith("vl_video_"):
                try:
                    os.rmdir(_parent)
                except OSError:
                    pass
