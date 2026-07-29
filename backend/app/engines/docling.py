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
        file_path_str = str(file_path)
        pdf = pdfium.PdfDocument(file_path_str)
        parts: list[str] = []
        img_idx = 0
        scanned_pages = 0

        # ── PDF 内置目录 ──
        toc = self._extract_toc(pdf)
        if toc:
            parts.append(toc)

        for i in range(len(pdf)):
            page = pdf[i]
            page_parts: list[str] = []
            page_imgs: list[tuple[float, str]] = []

            page_objs = list(page.get_objects())
            txt_n, img_n, other_n = self._classify_objs(page_objs)

            tp = page.get_textpage()
            text = tp.get_text_bounded()

            # ── 1. 文字 ──
            raw_paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()] if text.strip() else []
            if not raw_paragraphs:
                scanned_pages += 1

            # ── 2. 表格：pdfplumber 优先（精准），回退坐标推断 ──
            table_md, table_bbox = self._extract_tables(file_path_str, i, tp)
            if table_md:
                page_parts.append(table_md)

            # ── 3. 去重：表格区域内的段落不重复输出 ──
            for p in raw_paragraphs:
                if not self._overlaps_table(p, table_bbox, tp):
                    page_parts.append(p)

            # ── 3. 矢量图聚类渲染（排除表格包围盒内对象）──
            if other_n > 3 and other_n > txt_n * 0.15:
                other_rects = self._collect_other_rects(page_objs)
                # 排除与表格重叠的 rects
                if table_bbox:
                    tx0, ty0, tx1, ty1 = table_bbox
                    other_rects = [r for r in other_rects
                                   if not (r[0] >= tx0 and r[1] >= ty0 and r[2] <= tx1 and r[3] <= ty1)]
                clusters = self._cluster_rects(other_rects, gap=30) if other_rects else []
                pw, ph = page.get_size()
                page_area = pw * ph
                for cx0, cy0, cx1, cy1 in clusters:
                    # 聚类级别质量检查
                    cl_w, cl_h = cx1 - cx0, cy1 - cy0
                    if cl_w < 40 or cl_h < 40:
                        continue
                    # 统计簇内对象覆盖密度
                    cl_area = cl_w * cl_h
                    inner_area = sum((r[2]-r[0])*(r[3]-r[1]) for r in other_rects
                                     if r[0]>=cx0 and r[1]>=cy0 and r[2]<=cx1 and r[3]<=cy1)
                    density = inner_area / max(cl_area, 1)
                    # 密度太低 = 稀疏装饰，不是图表
                    if density < 0.03 and cl_area < page_area * 0.4:
                        continue
                    if cx1 - cx0 < 30 or cy1 - cy0 < 30:
                        continue
                    cl, cr, ct, cb = cx0, pw - cx1, cy0, ph - cy1
                    if cl < 0 or cr < 0 or ct < 0 or cb < 0:
                        continue
                    rw, rh = pw - cl - cr, ph - ct - cb
                    if rw < 30 or rh < 30:
                        continue
                    ratio = rw / max(rh, 1)
                    if (rw * rh) < page_area * 0.08 and (rw * rh) < 20000:
                        continue
                    if ratio > 15 or ratio < 0.07:
                        continue
                    try:
                        bitmap = page.render(scale=2.5, crop=(cl, cb, cr, ct))
                        pil = bitmap.to_pil()
                        alt = f"fig_{img_idx}"
                        if media_dir:
                            fname = f"fig_{img_idx:04d}.png"
                            pil.save(str(Path(media_dir) / fname), format="PNG")
                            page_parts.append(f"![{alt}]({fname})")
                        else:
                            buf = io.BytesIO()
                            pil.save(buf, format="PNG")
                            b64 = base64.b64encode(buf.getvalue()).decode()
                            page_parts.append(f"![{alt}](data:image/png;base64,{b64})")
                        img_idx += 1
                    except Exception:
                        pass

            # ── 嵌入图片（收集位置后按锚点插入）──
            for obj in page_objs:
                if not isinstance(obj, pdfium.PdfImage):
                    continue
                try:
                    bmp = obj.get_bitmap(render=True)
                    pil_img = bmp.to_pil()
                    if pil_img.size[0] <= 10 or pil_img.size[1] <= 10:
                        continue
                    alt = f"img_{img_idx}"
                    if media_dir:
                        fname = f"img_{img_idx:04d}.png"
                        pil_img.save(str(Path(media_dir) / fname), format="PNG")
                        md_ref = f"![{alt}]({fname})"
                    else:
                        buf = io.BytesIO()
                        pil_img.save(buf, format="PNG")
                        b64 = base64.b64encode(buf.getvalue()).decode()
                        md_ref = f"![{alt}](data:image/png;base64,{b64})"
                    img_idx += 1
                    # 获取图片在页面的 Y 位置
                    bounds = obj.get_bounds()
                    page_imgs.append((bounds[1], md_ref))
                except Exception:
                    pass

            # 按 Y 坐标将图片锚入段落之间
            if page_imgs and len(page_parts) > 1:
                page_parts = self._anchor_images(page_parts, page_imgs)
            elif page_imgs:
                page_parts.extend(md for _, md in page_imgs)

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
    def _extract_toc(pdf) -> str | None:
        """提取 PDF 内置书签目录 → Markdown 标题列表"""
        try:
            items = list(pdf.get_toc())
        except Exception:
            return None
        if not items:
            return None
        lines: list[str] = []
        for item in items:
            level = getattr(item, 'level', 1)
            title = getattr(item, 'title', '')
            if title.strip():
                prefix = "#" * min(level + 1, 4)  # level 1 → ##, level 2 → ###
                lines.append(f"{prefix} {title.strip()}")
        return "\n".join(lines) if lines else None

    @staticmethod
    def _anchor_images(page_parts: list[str], page_imgs: list[tuple[float, str]]) -> list[str]:
        """按图片 Y 坐标锚入段落之间：图片 Y 在两个段落之间时插入"""
        if len(page_parts) <= 1:
            return page_parts + [md for _, md in page_imgs]
        # 图片按 Y 排序
        page_imgs.sort(key=lambda x: x[0])
        result: list[str] = []
        img_iter = iter(page_imgs)
        next_img = next(img_iter, None)
        for pi, p in enumerate(page_parts):
            result.append(p)
            # 在第一段之后插入可能在它之后的图片
            while next_img and pi >= 0:
                result.append(next_img[1])
                next_img = next(img_iter, None)
            if next_img is None:
                break
        # 剩余图片放最后
        while next_img:
            result.append(next_img[1])
            next_img = next(img_iter, None)
        return result

    @staticmethod
    def _extract_tables(pdf_path: str, page_idx: int, tp) -> tuple[str | None, tuple | None]:
        """pdfplumber 提取表格（精准单元格文字），返回 (markdown, bbox)"""
        try:
            import pdfplumber
            with pdfplumber.open(pdf_path) as pp:
                if page_idx < len(pp.pages):
                    tables = pp.pages[page_idx].find_tables()
                    if tables:
                        all_md_parts: list[str] = []
                        for t in tables:
                            rows = t.extract()
                            if not rows or not rows[0] or len(rows[0]) < 2:
                                continue
                            rows = [[c or "" for c in r] for r in rows]
                            cols = len(rows[0])
                            lines: list[str] = []
                            for ri, row in enumerate(rows):
                                cells = [str(c).strip().replace("\n", " ")[:80] for c in row[:cols]]
                                lines.append("| " + " | ".join(cells) + " |")
                                if ri == 0:
                                    lines.append("| " + " | ".join(["---"] * cols) + " |")
                            if len(lines) > 2:
                                all_md_parts.append("\n".join(lines))
                        if all_md_parts:
                            return "\n\n".join(all_md_parts), None
        except Exception:
            pass
        return None, None

    @staticmethod
    def _detect_tables_fallback(tp) -> tuple[str | None, tuple | None]:
        """回退：pypdfium2 坐标推断表格"""
        md = PyPdfium2Engine._build_table_from_rects(tp)
        if md:
            bbox = PyPdfium2Engine._table_bbox(tp)
            return md, bbox
        return None, None

    @staticmethod
    def _overlaps_table(paragraph: str, table_bbox: tuple | None, tp) -> bool:
        """检查段落是否与表格区域重叠——是则跳过"""
        if table_bbox is None:
            return False
        return False  # 简化：不去重段落（pdfplumber 提取的文字不在 pypdfium2 段落中）

    @staticmethod
    def _build_table_from_rects(tp) -> str | None:
        """从 text rect 坐标推断表格，输出 GFM Markdown table"""
        n = tp.count_rects()
        if n < 4:
            return None

        # 收集所有 rect 的坐标
        rects: list[tuple[float, float, float, float]] = []
        for idx in range(n):
            r = tp.get_rect(idx)
            w, h = r[2] - r[0], r[3] - r[1]
            if w < 2 or h < 2:
                continue
            rects.append(r)

        if len(rects) < 4:
            return None

        # 按 Y 坐标分组（容差 5pt），每组内按 X 排序
        rects.sort(key=lambda r: (round(r[1] / 5) * 5, r[0]))
        rows: list[list[tuple]] = []
        current_row: list[tuple] = [rects[0]]
        current_y = round(rects[0][1] / 5) * 5

        for r in rects[1:]:
            y_bucket = round(r[1] / 5) * 5
            if abs(y_bucket - current_y) > 8:
                if current_row:
                    rows.append(sorted(current_row, key=lambda x: x[0]))
                current_row = [r]
                current_y = y_bucket
            else:
                current_row.append(r)
        if current_row:
            rows.append(sorted(current_row, key=lambda x: x[0]))

        if len(rows) < 2:
            return None

        # 严格检查：至少 2 行，每行至少 2 列，且多数行有 2+ 列
        col_counts = [len(r) for r in rows]
        multi_col_rows = sum(1 for c in col_counts if c >= 2)
        if max(col_counts) < 2 or multi_col_rows < len(rows) * 0.7:
            return None

        # 用中位数列数，中心点定位提取每格实际文字
        cols = sorted(col_counts)[len(col_counts) // 2]
        lines: list[str] = []
        for ri, row in enumerate(rows):
            cells: list[str] = []
            for ci in range(cols):
                if ci < len(row):
                    r = row[ci]
                    # 用格子中心点定位文字
                    cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
                    idx = tp.get_index(cx, cy, 20, 10)
                    if idx is not None and idx >= 0:
                        text = tp.get_text_range(index=idx, count=100).strip()
                        # 取到下一个格子开始处或换行处
                        text = text.split("\n")[0].strip()[:80]
                        cells.append(text)
                    else:
                        cells.append("")
                else:
                    cells.append("")
            lines.append("| " + " | ".join(cells) + " |")
            if ri == 0:
                lines.append("| " + " | ".join(["---"] * cols) + " |")

        return "\n".join(lines)

    @staticmethod
    def _table_bbox(tp) -> tuple[float, float, float, float] | None:
        """返回检测到的表格完整包围盒（TOPLEFT），用于从矢量聚类中排除表格区域"""
        n = tp.count_rects()
        if n < 4:
            return None
        rects: list[tuple[float, float, float, float]] = []
        for idx in range(n):
            r = tp.get_rect(idx)
            if r[2] - r[0] >= 2 and r[3] - r[1] >= 2:
                rects.append(r)
        if len(rects) < 4:
            return None
        rects.sort(key=lambda r: (round(r[1] / 5) * 5, r[0]))
        rows: list[list[tuple]] = []
        current: list[tuple] = [rects[0]]
        cy = round(rects[0][1] / 5) * 5
        for r in rects[1:]:
            yb = round(r[1] / 5) * 5
            if abs(yb - cy) > 8:
                rows.append(sorted(current, key=lambda x: x[0]))
                current = [r]; cy = yb
            else:
                current.append(r)
        if current:
            rows.append(sorted(current, key=lambda x: x[0]))
        if len(rows) < 2:
            return None
        # 放宽列数检查：允许少量不规则行
        col_counts = [len(r) for r in rows]
        median_cols = sorted(col_counts)[len(col_counts)//2]
        if median_cols < 2:
            return None
        # 返回表格完整包围盒
        all_rects = [r for row in rows for r in row]
        x0 = min(r[0] for r in all_rects) - 20
        y0 = min(r[1] for r in all_rects) - 10
        x1 = max(r[2] for r in all_rects) + 20
        y1 = max(r[3] for r in all_rects) + 10
        return (x0, y0, x1, y1)

    @staticmethod
    def _classify_objs(objs) -> tuple[int, int, int]:
        import pypdfium2 as pdfium
        texts = images = others = 0
        for obj in objs:
            if isinstance(obj, pdfium.PdfTextObj):
                texts += 1
            elif isinstance(obj, pdfium.PdfImage):
                images += 1
            else:
                others += 1
        return texts, images, others

    @staticmethod
    def _collect_other_rects(objs) -> list[tuple[float, float, float, float]]:
        import pypdfium2 as pdfium
        rects: list[tuple[float, float, float, float]] = []
        for obj in objs:
            if isinstance(obj, (pdfium.PdfTextObj, pdfium.PdfImage)):
                continue
            try:
                b = obj.get_bounds()
                w, h = b[2] - b[0], b[3] - b[1]
                if w < 2 or h < 2:
                    continue
                rects.append((b[0], b[1], b[2], b[3]))
            except Exception:
                pass
        return rects

    @staticmethod
    def _cluster_rects(rects: list[tuple], gap: float = 20) -> list[tuple]:
        """将空间相邻的矩形聚类，返回每个簇的包围盒"""
        if not rects:
            return []
        # 按 Y 排序
        sorted_rects = sorted(rects, key=lambda r: r[1])
        clusters: list[list[tuple]] = []
        current: list[tuple] = [sorted_rects[0]]
        for r in sorted_rects[1:]:
            # 和前一个比较：Y 间距 < gap → 同一簇
            prev = current[-1]
            if abs(r[1] - prev[1]) < gap or abs(r[1] - prev[3]) < gap:
                current.append(r)
            else:
                clusters.append(current)
                current = [r]
        clusters.append(current)
        # 合并每簇的包围盒
        result: list[tuple] = []
        for cluster in clusters:
            x0 = min(r[0] for r in cluster)
            y0 = min(r[1] for r in cluster)
            x1 = max(r[2] for r in cluster)
            y1 = max(r[3] for r in cluster)
            # 加少量 padding
            result.append((x0 - 2, y0 - 2, x1 + 2, y1 + 2))
        return result

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
