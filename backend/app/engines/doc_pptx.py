"""
python-pptx 引擎 — PPT .pptx 转录为 Markdown

逐 slide → 标题/正文/表格/备注 → MD。图片提取严重不足（~1张），
已由 markitdown（33张）替代为默认引擎。
"""

import base64
import logging
from pathlib import Path

from app.core.protocols import Engine
from app.engines.doc_types import DocResult

logger = logging.getLogger(__name__)


class PptxEngine(Engine):
    """python-pptx — PPT .pptx 转录为 Markdown

    逐 slide → 标题/正文/表格/备注 → MD。图片提取严重不足（~1张），
    已由 markitdown（33张）替代为默认引擎。
    """

    def __init__(self, text_only: bool = False):
        self.text_only = text_only
        self.capabilities = ["text", "table", "office"]  # image 已移除，提取不足

    def execute(self, file_path: str, **kwargs) -> DocResult:
        from pptx import Presentation
        from pptx.util import Inches, Pt
        from lxml import etree

        media_dir = kwargs.get("media_dir", "")
        img_counter = [0]

        prs = Presentation(file_path)
        parts: list[str] = []

        for si, slide in enumerate(prs.slides):
            slide_num = si + 1
            slide_parts: list[str] = [f"## Slide {slide_num}"]

            for shape in slide.shapes:
                if shape.has_text_frame:
                    text = shape.text_frame.text.strip()
                    if not text:
                        continue
                    # 标题占位符
                    if shape.is_placeholder and shape.placeholder_format.type == 1:  # TITLE
                        slide_parts.append(f"### {text}")
                    elif shape.is_placeholder and shape.placeholder_format.type == 2:  # SUBTITLE
                        slide_parts.append(f"#### {text}")
                    else:
                        slide_parts.append(text)

                elif shape.has_table:
                    table_md = self._convert_pptx_table(shape.table)
                    if table_md:
                        slide_parts.append(table_md)

                elif shape.shape_type == 13:  # Picture
                    if not self.text_only:
                        img_md = self._extract_pptx_image(shape, media_dir, img_counter)
                        if img_md:
                            slide_parts.append(img_md)

            # 演讲者备注
            try:
                notes = slide.notes_slide.notes_text_frame.text.strip()
                if notes:
                    slide_parts.append(f"\n> 📝 {notes}")
            except Exception:
                logger.warning("Failed to extract slide %d notes", exc_info=True, extra={"slide": slide_num})

            parts.append("\n\n".join(slide_parts))

        full_text = "\n\n---\n\n".join(p for p in parts if p)
        logger.info(
            "PptxEngine: %d slides, output %d chars, text_only=%s",
            len(prs.slides), len(full_text), self.text_only,
        )
        return DocResult(
            text=full_text,
            pages=len(prs.slides),
            engine="python-pptx" if not self.text_only else "python-pptx-text",
        )

    def _convert_pptx_table(self, table) -> str | None:
        """python-pptx Table → Markdown table"""
        rows = table.rows
        if not rows:
            return None
        lines: list[str] = []
        for ri, row in enumerate(rows):
            cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
            lines.append("| " + " | ".join(cells) + " |")
            if ri == 0:
                lines.append("| " + " | ".join(["---"] * len(cells)) + " |")
        return "\n".join(lines)

    def _extract_pptx_image(self, shape, media_dir: str, img_counter: list[int]) -> str | None:
        """从 Picture shape 提取图片 blob → 写文件（有 media_dir）或 base64（无 media_dir）。
        对标 PyMuPDF / DocxEngine 的双模式设计。"""
        try:
            blob = shape.image.blob
            ext = shape.image.content_type.split("/")[-1].lower()
            if ext == "jpeg":
                ext = "jpg"
            if media_dir:
                idx = img_counter[0]
                img_counter[0] = idx + 1
                fname = f"img_{idx:04d}.{ext}"
                (Path(media_dir) / fname).write_bytes(blob)
                return f"![slide image]({fname})"
            else:
                b64 = base64.b64encode(blob).decode()
                return f"![slide image](data:image/{ext};base64,{b64})"
        except Exception as e:
            logger.warning("PptxEngine 图片提取失败: %s", str(e)[:80])
            return None
