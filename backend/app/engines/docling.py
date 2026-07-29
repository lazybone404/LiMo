"""Docling + pypdfium2 引擎组 — MIT/BSD 协议 PDF 转录（零 AGPL 依赖）

- PyPdfium2Engine: pypdfium2 C 引擎直读文字层，毫秒级，不跑模型
- DoclingEngine: Docling ONNX 布局模型完整转录，带图片渲染
"""

from __future__ import annotations

import base64
import io
import logging
import re
from pathlib import Path

from app.core.protocols import Engine
from app.engines.doc import DocResult

logger = logging.getLogger(__name__)


class PyPdfium2Engine(Engine):
    """极速引擎 — pypdfium2 (BSD-3) C 引擎直读文字 + 嵌入图片。

    不跑任何 ONNX 模型，纯 C/Python。速度对标 pymupdf。
    输出 ## Page N 分页 + 段落 + 嵌入图片（原始质量）。
    """

    name = "pypdfium2"
    capabilities = ["text", "image", "pdf"]

    def execute(self, file_path: str, **kwargs) -> DocResult:
        import pypdfium2 as pdfium

        media_dir = kwargs.get("media_dir", "")
        pdf = pdfium.PdfDocument(str(file_path))
        parts: list[str] = []
        img_idx = 0
        scanned_pages = 0

        for i in range(len(pdf)):
            page = pdf[i]
            page_parts: list[str] = []

            # ── 文字层 ──
            tp = page.get_textpage()
            text = tp.get_text_bounded()

            # 尝试从 text rects 坐标推断表格
            table_md = self._detect_tables(tp)
            if table_md:
                page_parts.append(table_md)

            if text.strip():
                paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
                page_parts.extend(paragraphs)
            elif self._has_page_objects(page):
                # 无文字但有页面对象 → 可能是矢量图表
                page_parts.append("*[此页可能包含图表，建议用 docling 引擎重新转录]*")
            else:
                scanned_pages += 1

            # ── 嵌入图片 ──
            for obj in page.get_objects():
                if not isinstance(obj, pdfium.PdfImage):
                    continue
                try:
                    pil_img = obj.extract()
                    if pil_img and pil_img.size[0] > 10 and pil_img.size[1] > 10:
                        alt = f"img_{img_idx}"
                        if media_dir:
                            fname = f"img_{img_idx:04d}.png"
                            pil_img.save(str(Path(media_dir) / fname), format="PNG")
                            page_parts.append(f"![{alt}]({fname})")
                        else:
                            buf = io.BytesIO()
                            pil_img.save(buf, format="PNG")
                            b64 = base64.b64encode(buf.getvalue()).decode()
                            page_parts.append(f"![{alt}](data:image/png;base64,{b64})")
                        img_idx += 1
                except Exception:
                    pass

            if page_parts:
                parts.append("\n\n".join(page_parts))

        pdf.close()
        full_text = "\n\n".join(parts)
        is_scanned = len(parts) > 0 and scanned_pages > len(parts) * 0.5

        logger.info(
            "pypdfium2: %d pages, %d images, %d scanned, output %d chars",
            len(parts), img_idx, scanned_pages, len(full_text),
        )
        return DocResult(
            text=full_text, pages=len(parts), engine="pypdfium2",
            is_scanned=is_scanned, scanned_pages=scanned_pages,
        )

    @staticmethod
    def _detect_tables(tp) -> str | None:
        """从 text rect 坐标推断表格，输出 GFM Markdown table"""
        n = tp.count_rects()
        if n < 4:  # 至少 2 行 × 2 列
            return None

        # 收集所有 rect 的坐标和文本
        rects: list[tuple[float, float, float, float, str]] = []
        for idx in range(n):
            r = tp.get_rect(idx)
            text = tp.get_text_range(index=r.get_index(), count=r.count_chars())
            if not text.strip():
                continue
            bbox = r.get_bbox()
            rects.append((bbox[0], bbox[1], bbox[2], bbox[3], text.strip()))

        if len(rects) < 4:
            return None

        # 按 Y 坐标分组（容差 5pt）
        rects.sort(key=lambda r: (round(r[1] / 5) * 5, r[0]))
        rows: list[list[str]] = []
        current_row: list[str] = []
        current_y = round(rects[0][1] / 5) * 5

        for x0, y0, x1, y1, text in rects:
            y_bucket = round(y0 / 5) * 5
            if abs(y_bucket - current_y) > 5:
                if current_row:
                    rows.append(current_row)
                current_row = [text]
                current_y = y_bucket
            else:
                current_row.append(text)
        if current_row:
            rows.append(current_row)

        if len(rows) < 2:
            return None

        # 检查列数一致（至少 2 列）
        col_counts = [len(r) for r in rows]
        if len(set(col_counts)) > 1 or col_counts[0] < 2:
            return None

        # 构建 Markdown table
        cols = col_counts[0]
        lines: list[str] = []
        for ri, row in enumerate(rows):
            lines.append("| " + " | ".join(row) + " |")
            if ri == 0:
                lines.append("| " + " | ".join(["---"] * cols) + " |")

        return "\n".join(lines)

    @staticmethod
    def _has_page_objects(page) -> bool:
        """检测页面是否有可见对象（路径/图形等非文字非图片元素）"""
        try:
            objs = page.get_objects()
            return len(objs) > 0
        except Exception:
            return False

class DoclingEngine(Engine):
    """基于 Docling（IBM, MIT）+ pypdfium2（BSD-3）的 PDF → Markdown 引擎。"""

    name = "docling"
    capabilities = ["text", "table", "image", "pdf"]

    def __init__(self, text_only: bool = False):
        self.text_only = text_only
        if text_only:
            self.capabilities = ["text", "table", "pdf"]

    def execute(self, file_path: str, **kwargs) -> DocResult:
        from docling.document_converter import DocumentConverter
        import pypdfium2 as pdfium

        media_dir = kwargs.get("media_dir", "")
        file_path_str = str(file_path)

        # ── 1. Docling 布局分析 ──
        converter = DocumentConverter()
        result = converter.convert(file_path_str)
        docling_doc = result.document

        # ── 2. 原生 Markdown 输出 ──
        md = docling_doc.export_to_markdown(traverse_pictures=True)

        # ── 3. 图片注入 ──
        if not self.text_only and "<!-- image -->" in md:
            pictures = getattr(docling_doc, "pictures", []) or []
            if pictures:
                pdf = pdfium.PdfDocument(file_path_str)
                md = self._inject_images(md, pictures, pdf, media_dir)
                pdf.close()
            else:
                md = md.replace("<!-- image -->", "")
                logger.warning("Docling: placeholders found but no pictures")

        page_count = docling_doc.num_pages() if hasattr(docling_doc, "num_pages") else 0

        logger.info("Docling: %d pages, output %d chars", page_count, len(md))
        return DocResult(
            text=md, pages=page_count, engine="docling",
            is_scanned=False, scanned_pages=0,
        )

    def _inject_images(self, md: str, pictures, pdf, media_dir: str) -> str:
        image_refs: list[str] = []
        for picture in pictures:
            img_md = self._render_image_region(picture, pdf, media_dir, len(image_refs))
            if img_md:
                image_refs.append(img_md)
        if not image_refs:
            return md
        refs_iter = iter(image_refs)
        return re.sub(r"<!--\s*image\s*-->", lambda _: next(refs_iter, ""), md)

    def _render_image_region(self, picture, pdf, media_dir: str, img_idx: int) -> str | None:
        prov = None
        if hasattr(picture, "prov") and picture.prov:
            prov = picture.prov[0] if isinstance(picture.prov, list) else picture.prov
        if prov is None or not hasattr(prov, "bbox"):
            return None

        bbox = prov.bbox
        page_no = getattr(prov, "page_no", 0)

        try:
            page = pdf[page_no]
            page_w, page_h = page.get_size()
            x0, x1 = bbox.l, bbox.r
            y0 = page_h - bbox.t
            y1 = page_h - bbox.b

            w, h = x1 - x0, y1 - y0
            if w < 5 or h < 5 or x0 < 0 or y0 < 0:
                return None

            try:
                bitmap = page.render(scale=2.0, crop=(x0, y0, x1, y1))
            except Exception:
                full = page.render(scale=2.0)
                pil_full = full.to_pil()
                pil_img = pil_full.crop((
                    int(x0 * 2), int(y0 * 2), int(x1 * 2), int(y1 * 2),
                ))
                if pil_img.size[0] < 5 or pil_img.size[1] < 5:
                    return None
            else:
                pil_img = bitmap.to_pil()

            alt = f"fig_{img_idx}"
            if media_dir:
                fname = f"fig_{img_idx:04d}.png"
                pil_img.save(str(Path(media_dir) / fname), format="PNG")
                return f"![{alt}]({fname})"
            else:
                buf = io.BytesIO()
                pil_img.save(buf, format="PNG")
                b64 = base64.b64encode(buf.getvalue()).decode()
                return f"![{alt}](data:image/png;base64,{b64})"
        except Exception as e:
            logger.warning("Docling 图片渲染失败: %s", str(e)[:80])
            return None
