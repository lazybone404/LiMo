"""
Generator — LLM 生成编排器

职责：接收准备好的内容 → 构建 prompt → 调用 LLM → 后处理 → 返回 MarkdownView。
内容准备（VL/图片剥离/截断）已下沉到 ContentPreparer，
prompt 构建已下沉到 PromptBuilder。
"""

import asyncio
import logging
import os
import uuid
from pathlib import Path

from app.core.types import MarkdownView, Style, GenerateMode
from app.llm.service import LLMService

logger = logging.getLogger(__name__)

MAX_TOKENS = 65536


# ── 后处理（模块级工具函数） ──

def _post_process_content_markers(markdown: str) -> str:
    """将 *Content-[mm:ss] 转为安全的 markdown 链接格式，避免 * 被解析为斜体"""
    import re
    return re.sub(r'\*?Content-\[(\d{1,2}:\d{1,2})\]', r'[▶ \1](#)', markdown)


def _post_process_screenshots(markdown: str, video_path: str | None) -> str:
    """将 *Screenshot-[mm:ss] 标记替换为实际截图 URL"""
    if not video_path or not os.path.exists(video_path):
        return markdown

    try:
        from app.utils.screenshot_marker import extract_screenshot_timestamps
        from app.utils.video_helper import generate_screenshot

        matches = extract_screenshot_timestamps(markdown)
        if not matches:
            return markdown

        output_dir = os.path.join(os.getcwd(), "data", "screenshots")
        for idx, (marker, ts) in enumerate(matches):
            try:
                img_path = generate_screenshot(video_path, output_dir, ts, idx)
                filename = Path(img_path).name
                img_url = f"/static/screenshots/{filename}"
                markdown = markdown.replace(marker, f"![]({img_url})", 1)
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
        cancel_event: asyncio.Event | None = None,
    ) -> MarkdownView:
        """生成单个产物（notes 或 polished）。"""
        keep_refs = formats is not None and "images" in formats
        send_to_llm = image_analyze and keep_refs

        clean_text, image_urls, video_path = await self._prep.prepare(
            raw_text, vl_config, keep_refs,
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
                content = _post_process_screenshots(content, video_path)
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
        cancel_event: asyncio.Event | None = None,
    ) -> tuple[MarkdownView, MarkdownView]:
        """并行生成双产物（polished + notes）。Excel 生成 report + summary。"""
        keep_refs = formats is not None and "images" in formats
        send_to_llm = image_analyze and keep_refs

        clean_text, image_urls, video_path = await self._prep.prepare(
            raw_text, vl_config, keep_refs,
            send_to_llm=send_to_llm, optimize_images=image_optimize,
        )

        prompt_a, prompt_b = self._prompts.build_both(
            source_type, clean_text, title, style, formats, user_notes,
        )

        is_excel = source_type == "xlsx"
        logger.info("Generating %s in parallel...",
                     "report + summary" if is_excel else "polished + notes")

        try:
            task_a = self._llm.call(
                prompt_a, image_urls=image_urls, max_tokens=MAX_TOKENS,
                temperature=0.7, max_continuations=3,
                cancel_event=cancel_event,
            )
            task_b = self._llm.call(
                prompt_b, image_urls=image_urls,
                max_tokens=MAX_TOKENS // 2 if is_excel else MAX_TOKENS,
                temperature=0.7, max_continuations=3,
                cancel_event=cancel_event,
            )
            content_a, content_b = await asyncio.gather(task_a, task_b)

            content_a = _post_process_content_markers(content_a)
            content_b = _post_process_content_markers(content_b)
            if formats and "screenshot" in formats:
                content_b = _post_process_screenshots(content_b, video_path)
        finally:
            self._cleanup_video(video_path)

        if is_excel:
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
        else:
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
