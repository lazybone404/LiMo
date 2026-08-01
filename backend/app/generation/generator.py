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
from app.llm.service import LLMService

logger = logging.getLogger(__name__)

MAX_TOKENS = 65536


# ── 后处理（模块级工具函数） ──

def _post_process_content_markers(markdown: str) -> str:
    """Content-[mm:ss] 标记由前端渲染为时间戳 badge，后端不做替换。
    仅移除可能残留的前导 *（避免被 markdown 解析为斜体）。"""
    import re
    return re.sub(r'\*(Content-\[\d{1,2}:\d{1,2}\])', r'\1', markdown)


def _post_process_screenshots(markdown: str, video_path: str | None, doc_dir: str = "") -> str:
    """将 *Screenshot-[mm:ss] 标记替换为 assets/xxx 相对路径截图引用"""
    if not video_path or not os.path.exists(video_path):
        logger.info("原片截图：无视频文件，跳过（video_path=%s）", video_path)
        return markdown

    try:
        from app.utils.screenshot_marker import extract_screenshot_timestamps
        from app.utils.video_helper import generate_screenshot

        matches = extract_screenshot_timestamps(markdown)
        if not matches:
            logger.info("原片截图：LLM 未生成 *Screenshot-[mm:ss] 标记")
            return markdown

        logger.info("原片截图：找到 %d 个标记，开始 ffmpeg 抽帧", len(matches))
        output_dir = os.path.join(doc_dir or tempfile.mkdtemp(prefix="limo_ss_"), "assets")
        os.makedirs(output_dir, exist_ok=True)
        for idx, (marker, ts) in enumerate(matches):
            try:
                img_path = generate_screenshot(video_path, output_dir, ts, idx)
                filename = Path(img_path).name
                img_url = f"assets/{filename}"
                markdown = markdown.replace(marker, f"![]({img_url})", 1)
                logger.info("原片截图：%s → %s", marker, filename)
            except Exception as e:
                logger.warning("截图生成失败 ts=%ds: %s", ts, e)

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
    ):
        self._llm = llm_service
        self._prep = content_preparer
        self._prompts = prompt_builder

    # ── 公开接口 ──

    async def generate(
        self,
        raw_text: str,
        title: str = "",
        style: str = "detailed",
        source_type: str = "text",
        mode: str = "notes",
        formats: list[str] | None = None,
        vl_config: dict | None = None,
        user_notes: str = "",
        image_analyze: bool = False,
        image_optimize: bool = False,
        ocr_enabled: bool = False,
        ocr_model_id: int | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> MarkdownView:
        """生成单个产物（notes 或 polished）。"""
        keep_refs = formats is not None and "images" in formats
        send_to_llm = bool(image_analyze)

        # OCR 预处理：图片→文字→塞回原文（prepare 之前，需要原始图片路径）
        processed_text = raw_text
        if ocr_enabled and ocr_model_id:
            from app.generation.ocr_preprocessor import OCRPreprocessor
            ocr = OCRPreprocessor(self.config, ocr_model_id)
            processed_text = await ocr.process(
                raw_text, self._prep.store_root, doc_dir=self._prep.doc_dir)

        clean_text, image_urls, video_path = await self._prep.prepare(
            processed_text, vl_config, keep_refs,
            send_to_llm=send_to_llm, optimize_images=image_optimize,
        )

        prompt = self._prompts.build(
            source_type, mode, clean_text, title, style, formats, user_notes,
        )

        logger.info("Generating %s...", mode)
        try:
            content = await self._llm.call(
                prompt, image_urls=image_urls, max_tokens=MAX_TOKENS,
                temperature=0.7, max_continuations=3,
                cancel_event=cancel_event,
            )
            content = _post_process_content_markers(content)
            if formats and "screenshot" in formats:
                content = _post_process_screenshots(content, video_path, doc_dir=self._prep.doc_dir)
        finally:
            self._cleanup_video(video_path)

        return MarkdownView(
            id=f"md_{uuid.uuid4().hex[:12]}",
            transcript_id="",
            content=content,
            style=Style(style) if style in Style._value2member_map_ else Style.DETAILED,
            mode=GenerateMode(mode) if mode in GenerateMode._value2member_map_ else GenerateMode.NOTES,
        )

    async def generate_both(
        self,
        raw_text: str,
        title: str = "",
        style: str = "detailed",
        source_type: str = "text",
        formats: list[str] | None = None,
        vl_config: dict | None = None,
        user_notes: str = "",
        image_analyze: bool = False,
        image_optimize: bool = False,
        ocr_enabled: bool = False,
        ocr_model_id: int | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> tuple[MarkdownView, MarkdownView]:
        """并行生成双产物（polished + notes）。Excel 生成 report + summary。"""
        keep_refs = formats is not None and "images" in formats
        send_to_llm = bool(image_analyze)

        # OCR 预处理：图片→文字→塞回原文（prepare 之前，需要原始图片路径）
        processed_text = raw_text
        if ocr_enabled and ocr_model_id:
            from app.generation.ocr_preprocessor import OCRPreprocessor
            ocr = OCRPreprocessor(self.config, ocr_model_id)
            processed_text = await ocr.process(
                raw_text, self._prep.store_root, doc_dir=self._prep.doc_dir)

        clean_text, image_urls, video_path = await self._prep.prepare(
            processed_text, vl_config, keep_refs,
            send_to_llm=send_to_llm, optimize_images=image_optimize,
        )

        prompt_a, prompt_b = self._prompts.build_both(
            source_type, clean_text, title, style, formats, user_notes,
        )

        # Excel 只需一个数据摘要产物（分析报告已移除——LLM 无法做数值分析）
        if source_type == "xlsx":
            logger.info("Generating Excel summary...")
            try:
                content = await self._llm.call(
                    prompt_a, image_urls=image_urls,
                    max_tokens=MAX_TOKENS, temperature=0.7,
                    max_continuations=3, cancel_event=cancel_event,
                )
                content = _post_process_content_markers(content)
            finally:
                self._cleanup_video(video_path)
            mv = MarkdownView(
                id=f"md_{uuid.uuid4().hex[:12]}", transcript_id="",
                content=content,
                style=Style(style) if style in Style._value2member_map_ else Style.DETAILED,
                mode=GenerateMode.NOTES,
            )
            polished = MarkdownView(
                id=f"md_{uuid.uuid4().hex[:12]}", transcript_id="",
                content=content,
                style=Style(style) if style in Style._value2member_map_ else Style.DETAILED,
                mode=GenerateMode.POLISHED,
            )
            return polished, mv

        logger.info("Generating polished + notes in parallel...")
        try:
            task_a = self._llm.call(
                prompt_a, image_urls=image_urls, max_tokens=MAX_TOKENS,
                temperature=0.7, max_continuations=3,
                cancel_event=cancel_event,
            )
            task_b = self._llm.call(
                prompt_b, image_urls=image_urls,
                max_tokens=MAX_TOKENS, temperature=0.7,
                max_continuations=3, cancel_event=cancel_event,
            )
            content_a, content_b = await asyncio.gather(task_a, task_b)

            content_a = _post_process_content_markers(content_a)
            content_b = _post_process_content_markers(content_b)
            if formats and "screenshot" in formats:
                content_a = _post_process_screenshots(content_a, video_path, doc_dir=self._prep.doc_dir)
                content_b = _post_process_screenshots(content_b, video_path, doc_dir=self._prep.doc_dir)
        finally:
            self._cleanup_video(video_path)

        polished = MarkdownView(
            id=f"md_{uuid.uuid4().hex[:12]}", transcript_id="",
            content=content_a,
            style=Style(style) if style in Style._value2member_map_ else Style.DETAILED,
            mode=GenerateMode.POLISHED,
        )
        notes = MarkdownView(
            id=f"md_{uuid.uuid4().hex[:12]}", transcript_id="",
            content=content_b,
            style=Style(style) if style in Style._value2member_map_ else Style.DETAILED,
            mode=GenerateMode.NOTES,
        )
        return polished, notes

    @staticmethod
    def _cleanup_video(video_path: str | None):
        if video_path and os.path.exists(video_path):
            try:
                os.remove(video_path)
            except OSError:
                pass
