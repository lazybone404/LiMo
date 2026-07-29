"""pypdfium2 + Docling 引擎组 — MIT/BSD 协议 PDF 转录（零 AGPL 依赖）"""

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
    """pypdfium2 (BSD-3) + pdfplumber (MIT) 混合引擎。

    - 文字: pypdfium2 C 引擎 get_text_bounded
    - 表格: pdfplumber find_tables（精准单元格文字）
    - 嵌入图: pypdfium2 get_bitmap(render=True) 原始图片
    - 矢量图: 暂不处理，后续完善
    """

    name = "pypdfium2"
    capabilities = ["text", "image", "pdf"]

    def execute(self, file_path: str, **kwargs) -> DocResult:
        import pypdfium2 as pdfium

        media_dir = kwargs.get("media_dir", "")
        file_path_str = str(file_path)
        pdf = pdfium.PdfDocument(file_path_str)
        parts: list[str] = []
        img_idx = 0
        scanned_pages = 0

        # pdfplumber 实例复用
        import pdfplumber as _pl
        _pp = None
        try: _pp = _pl.open(file_path_str)
        except Exception: pass

        # PDF 内置目录
        toc = self._extract_toc(pdf)
        if toc: parts.append(toc)

        for i in range(len(pdf)):
            page = pdf[i]
            page_parts: list[str] = []
            pw, ph = page.get_size()
            tp = page.get_textpage()
            text = tp.get_text_bounded()

            # ── 1. 文字 ──
            if text.strip():
                page_parts.extend(p.strip() for p in text.split("\n\n") if p.strip())
            else:
                scanned_pages += 1

            # ── 2. 表格 (pdfplumber) ──
            table_md = self._extract_tables(_pp, i)
            if table_md:
                page_parts.insert(0, table_md)

            # ── 3. 嵌入图（收集 Y 坐标）──
            page_imgs: list[tuple[float, str]] = []
            for obj in page.get_objects():
                if not isinstance(obj, pdfium.PdfImage): continue
                try:
                    bmp = obj.get_bitmap(render=True)
                    pil_img = bmp.to_pil()
                    if pil_img.size[0] <= 10 or pil_img.size[1] <= 10: continue
                    bounds = obj.get_bounds()
                    img_y = bounds[1]  # TOPLEFT Y
                    if media_dir:
                        fname = f"img_{img_idx:04d}.png"
                        pil_img.save(str(Path(media_dir) / fname), format="PNG")
                        md_ref = f"![img_{img_idx}]({fname})"
                    else:
                        buf = io.BytesIO(); pil_img.save(buf, format="PNG")
                        md_ref = f"![img_{img_idx}](data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()})"
                    page_imgs.append((img_y, md_ref))
                    img_idx += 1
                except Exception:
                    pass

            # 按 Y 坐标将图片锚入段落之间
            if page_imgs and len(page_parts) > 1:
                page_imgs.sort(key=lambda x: x[0])
                # 从 text rects 获取段落分界 Y 值
                para_ys = self._paragraph_boundaries(tp, len(page_parts))
                anchored: list[str] = []
                img_idx_inner = 0
                for pi, p in enumerate(page_parts):
                    # 插入所有 Y 小于当前段落底部的图片
                    boundary = para_ys[pi] if pi < len(para_ys) else ph
                    while img_idx_inner < len(page_imgs) and page_imgs[img_idx_inner][0] < boundary:
                        anchored.append(page_imgs[img_idx_inner][1])
                        img_idx_inner += 1
                    anchored.append(p)
                while img_idx_inner < len(page_imgs):
                    anchored.append(page_imgs[img_idx_inner][1])
                    img_idx_inner += 1
                page_parts = anchored
            elif page_imgs:
                page_parts.extend(md for _, md in page_imgs)

            if page_parts:
                parts.append("\n\n".join(page_parts))

        pdf.close()
        if _pp:
            try: _pp.close()
            except Exception: pass

        full_text = "\n\n".join(parts)
        is_scanned = len(parts) > 0 and scanned_pages > len(parts) * 0.5
        logger.info("pypdfium2: %d pages, %d images, %d scanned, %d chars",
                    len(parts), img_idx, scanned_pages, len(full_text))
        return DocResult(text=full_text, pages=len(parts), engine="pypdfium2",
                         is_scanned=is_scanned, scanned_pages=scanned_pages)

    # ── helper methods ──

    @staticmethod
    def _extract_toc(pdf) -> str | None:
        try:
            items = list(pdf.get_toc())
        except Exception:
            return None
        if not items: return None
        lines: list[str] = []
        for item in items:
            level = getattr(item, 'level', 1)
            title = getattr(item, 'title', '')
            if title.strip():
                lines.append(f"{'#' * min(level + 1, 4)} {title.strip()}")
        return "\n".join(lines) if lines else None

    @staticmethod
    def _paragraph_boundaries(tp, n_paras: int) -> list[float]:
        """从 text rects 估算段落分界 Y 值（TOPLEFT），用于图片锚入"""
        try:
            n = tp.count_rects()
            if n < n_paras: return []
            rects = [tp.get_rect(i) for i in range(n) if tp.get_rect(i)[3] - tp.get_rect(i)[1] > 0]
            if not rects: return []
            rects.sort(key=lambda r: r[1])  # sort by Y
            # 按 Y 间距分组，找段落间隙
            gaps = []
            for i in range(1, len(rects)):
                gap = rects[i][1] - rects[i-1][3]
                if gap > 10:  # 段落间隙 > 10pt
                    gaps.append((rects[i-1][3] + rects[i][1]) / 2)
            return sorted(gaps)[:n_paras - 1]
        except Exception:
            return []

    @staticmethod
    def _extract_tables(pp, page_idx: int) -> str | None:
        try:
            if not pp or page_idx >= len(pp.pages): return None
            tables = pp.pages[page_idx].find_tables()
            if not tables: return None
            all_md: list[str] = []
            for t in tables:
                rows = t.extract()
                if not rows or not rows[0] or len(rows[0]) < 2: continue
                rows = [[c or "" for c in r] for r in rows]
                cols = len(rows[0])
                lines: list[str] = []
                for ri, row in enumerate(rows):
                    cells = [str(c).strip().replace("\n", " ")[:80] for c in row[:cols]]
                    lines.append("| " + " | ".join(cells) + " |")
                    if ri == 0: lines.append("| " + " | ".join(["---"] * cols) + " |")
                if len(lines) > 2: all_md.append("\n".join(lines))
            return "\n\n".join(all_md) if all_md else None
        except Exception:
            return None

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

        converter = DocumentConverter()
        result = converter.convert(file_path_str)
        docling_doc = result.document

        md = docling_doc.export_to_markdown(traverse_pictures=True)

        if not self.text_only and "<!-- image -->" in md:
            pictures = getattr(docling_doc, "pictures", []) or []
            if pictures:
                pdf = pdfium.PdfDocument(file_path_str)
                md = self._inject_images(md, pictures, pdf, media_dir)
                pdf.close()
            else:
                md = md.replace("<!-- image -->", "")

        page_count = docling_doc.num_pages() if hasattr(docling_doc, "num_pages") else 0
        logger.info("Docling: %d pages, output %d chars", page_count, len(md))
        return DocResult(text=md, pages=page_count, engine="docling", is_scanned=False, scanned_pages=0)

    def _inject_images(self, md: str, pictures, pdf, media_dir: str) -> str:
        image_refs: list[str] = []
        for picture in pictures:
            img_md = self._render_image_region(picture, pdf, media_dir, len(image_refs))
            if img_md: image_refs.append(img_md)
        if not image_refs: return md
        refs_iter = iter(image_refs)
        return re.sub(r"<!--\s*image\s*-->", lambda _: next(refs_iter, ""), md)

    def _render_image_region(self, picture, pdf, media_dir: str, img_idx: int) -> str | None:
        prov = None
        if hasattr(picture, "prov") and picture.prov:
            prov = picture.prov[0] if isinstance(picture.prov, list) else picture.prov
        if prov is None or not hasattr(prov, "bbox"): return None

        bbox = prov.bbox
        page_no = getattr(prov, "page_no", 0)

        try:
            page = pdf[page_no]
            page_w, page_h = page.get_size()
            x0, x1 = bbox.l, bbox.r
            y0 = page_h - bbox.t
            y1 = page_h - bbox.b

            w, h = x1 - x0, y1 - y0
            if w < 5 or h < 5 or x0 < 0 or y0 < 0: return None

            try:
                bitmap = page.render(scale=2.0, crop=(x0, y0, x1, y1))
            except Exception:
                full = page.render(scale=2.0)
                pil_full = full.to_pil()
                pil_img = pil_full.crop((int(x0 * 2), int(y0 * 2), int(x1 * 2), int(y1 * 2)))
                if pil_img.size[0] < 5 or pil_img.size[1] < 5: return None
            else:
                pil_img = bitmap.to_pil()

            alt = f"fig_{img_idx}"
            if media_dir:
                fname = f"fig_{img_idx:04d}.png"
                pil_img.save(str(Path(media_dir) / fname), format="PNG")
                return f"![{alt}]({fname})"
            else:
                buf = io.BytesIO(); pil_img.save(buf, format="PNG")
                return f"![{alt}](data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()})"
        except Exception as e:
            logger.warning("Docling image failed: %s", str(e)[:80])
            return None
