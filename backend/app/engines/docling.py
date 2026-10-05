"""pypdfium2 + Docling 引擎组 — MIT/BSD 协议 PDF 转录（零 AGPL 依赖）"""

from __future__ import annotations

import base64
import io
import logging
import re
from pathlib import Path

from app.core.protocols import Engine
from app.engines.doc_types import DocResult

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

        # pdfplumber 实例复用（可选依赖）
        _pp = None
        try:
            import pdfplumber as _pl
            _pp = _pl.open(file_path_str)
        except Exception as e:
            # P1-16 triage（可保留，但必须留痕）：这是**能力级**降级——pdfplumber 没装
            # 或这个 PDF 它打不开，后果是**整份文档的表格都不做提取**（文字与图片照常）。
            # 不抛错是对的（表格是增量能力），但静默是错的：用户会以为「这份 PDF 没有表格」。
            logger.warning("pdfplumber 打开失败，PDF 表格提取整体跳过：%s", str(e)[:80])

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
                except Exception as e:
                    # P1-16（**修**）：单张嵌入图提取失败 → 这张图**从产物里消失**。
                    # 此前是 `pass`：图没了、日志没有、用户零提示，只表现为「这份 PDF
                    # 的图好像少了」——而 PDF 的嵌入图常是图表/公式，丢了正文就断链。
                    #
                    # ⚠️ 形态与 `doc_docx.py:218` 的图片提取失败**一致**（warning + 跳过，
                    #    **不**往正文插占位）：正文里没有任何内部标记需要被消费，所以这里
                    #    不是「零残留」场景（对照 P1-07 的截图 marker——那里必须替换，
                    #    因为 `Screenshot-[mm:ss]` 是内部标记，留着会被前端渲染成 badge）。
                    logger.warning("pypdfium2 第 %d 页图片提取失败（跳过该图）：%s",
                                   i + 1, str(e)[:80])

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
            except Exception:
                pass  # P1-16 triage（保留）：close 失败无副作用——句柄随进程回收，
                      # 不影响已经生成的产物。记日志只会制造噪音。

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
            # P1-16 triage（保留）：**没有目录是绝大多数 PDF 的常态**，不是异常状态。
            # 这里 `return None` 与「目录为空」走同一条路（下方 `if not items`），
            # 记 warning 等于对每一份无目录的 PDF 刷一条噪音。
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
            # P1-16 triage（保留）：空列表 = **不做 Y 坐标锚定**，图片退化为落在页面
            # 文字之后（`elif page_imgs:` 分支）。这是**排版**退化，不是内容丢失——
            # 图还在、顺序也对，只是没插进段落之间。不值得为它打断转录。
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
        except Exception as e:
            # P1-16（**修**）：与上面单图失败同病——这一页的表格**凭空消失**，而返回值
            # 是 `None`，与「这页本来就没有表格」**不可区分**（调用方 `if table_md:`
            # 只看真假）。用户看到的是「这份 PDF 的表格没被提取出来」，无从知道是该换
            # 引擎（pdf-image 走 OCR）还是这份文件确实没有表格。
            logger.warning("pdfplumber 第 %d 页表格提取失败（该页表格丢失）：%s",
                           page_idx + 1, str(e)[:80])
            return None
