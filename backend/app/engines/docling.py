"""Docling 引擎 — MIT 协议 PDF 转录（零 AGPL 依赖）

基于 IBM Docling (MIT) 的 ONNX 布局模型做文字+表格+标题提取，
图片区域用 pypdfium2 (BSD-3) 裁剪渲染，完全不依赖 pymupdf (AGPL)。

标记为实验性引擎，默认不绑定。用户可在设置页手动切换。
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


class DoclingEngine(Engine):
    """基于 Docling（IBM, MIT）+ pypdfium2（BSD-3）的 PDF → Markdown 引擎。

    capabilities: text, table, image, pdf
    """

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
                # 有占位符但 pictures 为空 → 清除占位符避免原样显示
                md = md.replace("<!-- image -->", "")
                logger.warning("Docling: <!-- image --> placeholders found but no pictures in document")

        page_count = docling_doc.num_pages() if hasattr(docling_doc, "num_pages") else 0

        logger.info(
            "Docling: %d pages, output %d chars",
            page_count, len(md),
        )
        return DocResult(
            text=md,
            pages=page_count,
            engine="docling",
            is_scanned=False,
            scanned_pages=0,
        )

    # ── 图片注入 ────────────────────────────────────────

    def _inject_images(self, md: str, pictures, pdf, media_dir: str) -> str:
        """将 Docling 检测到的图片区域渲染为 PNG，替换 <!-- image --> 占位符"""
        import pypdfium2 as pdfium

        image_refs: list[str] = []
        for picture in pictures:
            img_md = self._render_image_region(picture, pdf, media_dir, len(image_refs))
            if img_md:
                image_refs.append(img_md)

        if not image_refs:
            return md

        # 替换占位符：每个 <!-- image --> 对应一张图片
        refs_iter = iter(image_refs)
        return re.sub(r"<!--\s*image\s*-->", lambda _: next(refs_iter, ""), md)

    def _render_image_region(
        self, picture, pdf, media_dir: str, img_idx: int,
    ) -> str | None:
        """用 pypdfium2 按 Docling 检测到的 bbox 裁剪渲染图片区域"""
        # 获取区域坐标
        prov = None
        if hasattr(picture, "prov") and picture.prov:
            prov = picture.prov[0] if isinstance(picture.prov, list) else picture.prov

        if prov is None or not hasattr(prov, "bbox"):
            return None

        bbox = prov.bbox
        page_no = getattr(prov, "page_no", 0)

        # bbox 格式: Docling 可能用 TOPLEFT 原点，需转换到 PDF BOTTOMLEFT
        try:
            page = pdf[page_no]
            page_w, page_h = page.get_size()

            x0, x1 = bbox.l, bbox.r
            # Docling TOPLEFT → PDF BOTTOMLEFT：翻转 Y 轴
            y0 = page_h - bbox.t
            y1 = page_h - bbox.b

            w, h = x1 - x0, y1 - y0
            if w < 5 or h < 5 or x0 < 0 or y0 < 0:
                return None

            # 裁剪渲染 (~144 DPI)
            try:
                bitmap = page.render(scale=2.0, crop=(x0, y0, x1, y1))
            except Exception:
                # crop 超界时回退：渲染整个页面再裁剪
                full = page.render(scale=2.0)
                pil_full = full.to_pil()
                # 坐标换算到渲染像素
                ratio = 2.0
                pil_img = pil_full.crop((
                    int(x0 * ratio), int(y0 * ratio),
                    int(x1 * ratio), int(y1 * ratio),
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

