"""
文档引擎 — PyMuPDF + MarkItDown
转录层：将 PDF/Office 转为 Markdown（文字+表格+图片）
"""

import base64
import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.core.protocols import Engine

logger = logging.getLogger(__name__)




@dataclass
class DocResult:
    text: str
    pages: int = 1
    engine: str = ""
    is_scanned: bool = False      # 大部分页面无文字层 → 扫描件
    scanned_pages: int = 0        # 无文字层的页数


class PyMuPDFEngine(Engine):
    """PyMuPDF — 电子版 PDF 转录为 Markdown

    text_only=True → 只提取文字层 + 表格，跳过图片嵌入和扫描 OCR
    text_only=False → 文字 + 表格 + 内嵌图片 + 扫描页自动 OCR
    """

    def __init__(self, text_only: bool = False):
        self.text_only = text_only
        self.capabilities = ["text", "table", "image", "pdf"] if not text_only else ["text", "table", "pdf"]

    def execute(self, file_path: str, **kwargs) -> DocResult:
        import fitz

        media_dir = kwargs.get("media_dir", "")  # 指定则写文件引用，否则 fallback base64

        doc = fitz.open(str(file_path))
        parts: list[str] = []
        scanned_pages = 0
        img_idx = 0

        for i, page in enumerate(doc):
            page_num = i + 1
            page_parts: list[str] = [f"## Page {page_num}"]

            # 1. 文字层
            text = page.get_text()
            has_text = bool(text.strip())
            if has_text:
                page_parts.append(text)
            else:
                scanned_pages += 1

            # 2. 表格识别（pymupdf 原生，不依赖 LLM）
            try:
                tabs = page.find_tables()
                for t in tabs:
                    md_table = t.to_markdown()
                    if md_table.strip():
                        page_parts.append("\n" + md_table.strip())
            except Exception as e:
                logger.warning("表格提取失败 p%d: %s", page_num, str(e)[:80])

            # 3. 嵌入图片（仅非 text_only 模式）
            embedded_img_count = 0
            if not self.text_only:
                try:
                    images = page.get_images(full=True)
                    for img in images:
                        xref = img[0]
                        base = doc.extract_image(xref)
                        ext = (base.get("ext") or "png").lower()
                        if ext in ("png", "jpeg", "jpg"):
                            embedded_img_count += 1
                            img_data = base["image"]
                            alt = f"page{page_num}_img"
                            if media_dir:
                                fname = f"img_{img_idx:04d}.{ext}"
                                img_idx += 1
                                (Path(media_dir) / fname).write_bytes(img_data)
                                page_parts.append(f"\n![{alt}]({fname})\n")
                            else:
                                b64 = base64.b64encode(img_data).decode()
                                page_parts.append(f"\n![{alt}](data:image/{ext};base64,{b64})\n")
                except Exception as e:
                    logger.warning("图片提取失败 p%d: %s", page_num, str(e)[:80])


            parts.append("\n\n".join(page_parts))

        pages = doc.page_count
        doc.close()

        full_text = "\n\n---\n\n".join(parts)
        is_scanned = pages > 0 and scanned_pages > pages * 0.5

        logger.info(
            "PyMuPDF: %d pages, %d scanned, is_scanned=%s, text_only=%s, output %d chars",
            pages, scanned_pages, is_scanned, self.text_only, len(full_text),
        )
        return DocResult(
            text=full_text,
            pages=pages,
            engine="pymupdf" if not self.text_only else "pymupdf-text",
            is_scanned=is_scanned if not self.text_only else False,  # text_only 不触发 OCR
            scanned_pages=scanned_pages,
        )


class PyMuPDF4LLMEngine(Engine):
    """PDF 引擎 — 基于 pymupdf4llm，自动处理文字+表格+嵌入图+矢量图"""

    name = "pymupdf4llm"
    capabilities = ["text", "table", "image", "pdf"]

    def execute(self, file_path: str, **kwargs) -> DocResult:
        import pymupdf4llm
        import fitz

        media_dir = kwargs.get("media_dir", "")

        md = pymupdf4llm.to_markdown(
            str(file_path),
            write_images=True,
            image_path=media_dir if media_dir else None,
            image_format="png",
            dpi=150,
        )

        doc = fitz.open(str(file_path))
        pages = doc.page_count
        doc.close()

        # 统计扫描页（暂无有效检测方式，设为 0）
        scanned_pages = 0

        logger.info(
            "pymupdf4llm: %d pages, output %d chars",
            pages, len(md),
        )
        return DocResult(
            text=md,
            pages=pages,
            engine="pymupdf4llm",
            is_scanned=False,
            scanned_pages=scanned_pages,
        )


class MarkItDownEngine(Engine):
    """MarkItDown（微软开源）— DOCX / PPTX / XLSX / EPUB 转 Markdown

    全自动处理文字+表格+嵌入图片，输出带内嵌图片的 Markdown。
    PDF 用 pymupdf4llm，EPUB 章节提取用 ebooklib（EpubEngine）。
    """

    capabilities = ["text", "table", "image", "office"]

    def execute(self, file_path: str, **kwargs) -> DocResult:
        from markitdown import MarkItDown

        media_dir = kwargs.get("media_dir", "")
        md = MarkItDown()
        result = md.convert(str(file_path))
        text = result.text_content

        # 如果有 media_dir，把内嵌 base64 图片写出为独立文件
        if media_dir:
            import base64, re
            from pathlib import Path
            img_idx = 0
            def _save_base64(m):
                nonlocal img_idx
                alt = m.group(1) or "img"
                b64 = m.group(2)
                try:
                    data = base64.b64decode(b64)
                    ext = ".png"
                    if data[:4] == b'\xff\xd8\xff\xe0' or data[:4] == b'\xff\xd8\xff\xe1':
                        ext = ".jpg"
                    elif data[:4] == b'\x89PNG':
                        ext = ".png"
                    fname = f"img_{img_idx:04d}{ext}"
                    img_idx += 1
                    (Path(media_dir) / fname).write_bytes(data)
                    return f"![{alt}]({fname})"
                except Exception:
                    return m.group(0)
            text = re.sub(r'!\[([^\]]*)\]\(data:image\/[^;]+;base64,([^)]+)\)', _save_base64, text)

        return DocResult(
            text=text,
            pages=1,
            engine="markitdown",
        )


class DocxEngine(Engine):
    """python-docx — Word .docx 转录为 Markdown

    对标 PyMuPDF：文字层 + 表格 + 内嵌图片 → 富 MD。
    按文档流顺序遍历 body 元素，不区分页面。

    text_only=True → 跳过图片 base64 编码，仅输出文字 + 表格。
    """

    def __init__(self, text_only: bool = False):
        self.text_only = text_only
        self.capabilities = ["text", "table", "image", "office"] if not text_only else ["text", "table", "office"]

    # XML 命名空间
    _NS = {
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
        "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
        "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
        "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
        "v": "urn:schemas-microsoft-com:vml",
        "wpg": "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup",
    }

    def execute(self, file_path: str, **kwargs) -> DocResult:
        from docx import Document
        from lxml import etree

        media_dir = kwargs.get("media_dir", "")
        # 图片计数器（跨 paragraphs 共享）
        img_counter = [0]

        doc = Document(file_path)
        parts: list[str] = []
        table_idx = 0

        for child in doc.element.body:
            tag = etree.QName(child).localname if isinstance(child.tag, str) else ""

            if tag == "p":
                result = self._convert_paragraph(child, doc, media_dir, img_counter)
                if result:
                    parts.append(result)

            elif tag == "tbl":
                if table_idx < len(doc.tables):
                    result = self._convert_table(doc.tables[table_idx])
                    if result:
                        parts.append(result)
                    table_idx += 1

            elif tag == "sdt":
                # 结构化文档标签 → 递归处理内部元素
                inner = self._convert_sdt(child, doc, media_dir, img_counter)
                if inner:
                    parts.append(inner)

        logger.info(
            "DocxEngine: %d blocks, output %d chars, file=%s, text_only=%s",
            len(parts), sum(len(p) for p in parts), Path(file_path).name, self.text_only,
        )
        return DocResult(
            text="\n\n".join(p for p in parts if p),
            pages=len(doc.sections),
            engine="python-docx" if not self.text_only else "python-docx-text",
        )

    # ── 段落转换 ──

    def _convert_paragraph(self, p_elem, doc, media_dir: str = "", img_counter: list[int] | None = None) -> str | None:
        """将 w:p 元素转为 Markdown"""
        from docx.text.paragraph import Paragraph

        para = Paragraph(p_elem, doc)
        style_name = (para.style.name or "").lower() if para.style else ""

        # 跳过空段落
        text = para.text.strip()
        has_drawing = self._has_drawing(p_elem) and not self.text_only

        if not text and not has_drawing:
            return None

        # 标题
        if style_name.startswith("heading"):
            level = self._heading_level(style_name)
            prefix = "#" * min(level, 6)
            return f"{prefix} {text}"

        # 列表项 — 保留缩进
        if style_name.startswith("list") or self._has_numbering(p_elem):
            return self._format_list_item(para, media_dir, img_counter)

        # 正文段落 — 保留行内格式
        formatted = self._format_runs(para, media_dir, img_counter)
        if not formatted.strip():
            return None
        return formatted

    def _heading_level(self, style_name: str) -> int:
        """从样式名提取标题级别"""
        for ch in style_name:
            if ch.isdigit():
                return int(ch)
        return 1

    def _has_drawing(self, p_elem) -> bool:
        """段落是否包含图片/drawing"""
        return len(p_elem.findall(".//wp:inline", self._NS)) > 0 or \
               len(p_elem.findall(".//wp:anchor", self._NS)) > 0 or \
               len(p_elem.findall(".//w:drawing", self._NS)) > 0

    def _has_numbering(self, p_elem) -> bool:
        """段落是否有编号属性"""
        numPr = p_elem.find(".//w:pPr/w:numPr", self._NS)
        return numPr is not None

    # ── 行内格式 ──

    def _format_runs(self, para, media_dir: str = "", img_counter: list[int] | None = None) -> str:
        """将段落 runs 转为带格式的 Markdown 行内文本"""
        result: list[str] = []

        for run in para.runs:
            t = run.text
            if not t:
                # 检查行内图片（text_only 模式跳过）
                if not self.text_only:
                    img_md = self._extract_inline_image(run, media_dir, img_counter)
                    if img_md:
                        result.append(img_md)
                continue

            # 转义 Markdown 特殊字符
            t = self._escape_md(t)

            if run.bold and run.italic:
                t = f"***{t}***"
            elif run.bold:
                t = f"**{t}**"
            elif run.italic:
                t = f"*{t}*"

            if run.underline:
                t = f"<u>{t}</u>"

            result.append(t)

        # 合并 runs 并清理多余空白
        text = "".join(result).strip()
        # 合并连续空行
        import re
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text

    def _extract_inline_image(self, run, media_dir: str = "", img_counter: list[int] | None = None) -> str | None:
        """从 run 中提取行内图片 → 文件引用或 base64"""
        drawings = run._element.findall(".//w:drawing", self._NS)
        if not drawings:
            return None

        parts: list[str] = []
        for drawing in drawings:
            for inline in drawing.findall(".//wp:inline", self._NS) + \
                         drawing.findall(".//wp:anchor", self._NS):
                blips = inline.findall(".//a:blip", self._NS)
                for blip in blips:
                    embed = blip.get(
                        "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
                    )
                    if embed:
                        img_md = self._extract_image(run, embed, media_dir, img_counter)
                        if img_md:
                            parts.append(img_md)
        return "\n".join(parts) if parts else None

    def _extract_image(self, run, rId: str, media_dir: str = "", img_counter: list[int] | None = None) -> str | None:
        """通过关系 ID 获取图片 → 写文件或 base64"""
        try:
            part = run.part
            if rId not in part.rels:
                return None
            image_part = part.rels[rId].target_part
            blob = image_part.blob
            ext = image_part.content_type.split("/")[-1].lower()
            if ext == "jpeg":
                ext = "jpg"
            if media_dir and img_counter is not None:
                idx = img_counter[0]
                img_counter[0] = idx + 1
                fname = f"img_{idx:04d}.{ext}"
                (Path(media_dir) / fname).write_bytes(blob)
                return f"![image]({fname})"
            else:
                b64 = base64.b64encode(blob).decode()
                return f"![image](data:image/{ext};base64,{b64})"
        except Exception as e:
            logger.warning("DocxEngine 图片提取失败: %s", str(e)[:80])
            return None

    # ── 表格 ──

    def _convert_table(self, table) -> str | None:
        """python-docx Table → Markdown table"""
        rows = table.rows
        if not rows:
            return None

        lines: list[str] = []
        merge_notes: list[str] = []

        for ri, row in enumerate(rows):
            cells: list[str] = []
            for ci, cell in enumerate(row.cells):
                text = cell.text.strip().replace("\n", " ")
                # 检测合并单元格
                grid_span = getattr(cell, 'grid_span', 1)
                if grid_span > 1:
                    merge_notes.append(f"> ⚠️ Row {ri+1} Col {ci+1}: 合并 {grid_span} 列")
                cells.append(text)
            lines.append("| " + " | ".join(cells) + " |")
            if ri == 0:
                lines.append("| " + " | ".join(["---"] * len(cells)) + " |")

        md = "\n".join(lines)
        if merge_notes:
            md = md + "\n" + "\n".join(merge_notes)
        return md

    # ── SDT（结构化文档标签）──

    def _convert_sdt(self, sdt_elem, doc, media_dir: str = "", img_counter: list[int] | None = None) -> str | None:
        """递归处理 sdt 内的段落/表格"""
        parts: list[str] = []
        from lxml import etree

        for child in sdt_elem:
            tag = etree.QName(child).localname if isinstance(child.tag, str) else ""
            if tag == "p":
                r = self._convert_paragraph(child, doc, media_dir, img_counter)
                if r:
                    parts.append(r)
            elif tag == "tbl":
                # SDT 内嵌表格——尝试通过 inner_content 匹配
                inner = child.findall(".//w:tr", self._NS)
                if inner:
                    parts.append(f"> [内嵌表格: {len(inner)} 行]")

        return "\n\n".join(parts) if parts else None

    # ── 列表 ──

    def _format_list_item(self, para, media_dir: str = "", img_counter: list[int] | None = None) -> str:
        """将列表段落格式化为 Markdown 列表"""
        text = self._format_runs(para, media_dir, img_counter)
        if not text.strip():
            return None
        # 检查缩进级别（通过 ind 属性）
        ind = para.paragraph_format.left_indent
        depth = 0
        if ind:
            # 720 EMU ≈ 0.25 inch → 一级缩进
            depth = max(0, round(ind / 720000))
        prefix = "    " * depth + "- "
        return prefix + text

    # ── 辅助 ──

    @staticmethod
    def _escape_md(text: str) -> str:
        """转义可能被误解析的 Markdown 字符"""
        # 保护已存在的 Markdown 链接/图片语法
        import re
        # 简单处理：反引号内的内容不转义
        return text.replace("\\", "\\\\")


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
                import base64
                b64 = base64.b64encode(blob).decode()
                return f"![slide image](data:image/{ext};base64,{b64})"
        except Exception as e:
            logger.warning("PptxEngine 图片提取失败: %s", str(e)[:80])
            return None


class PDFOCREngine(Engine):
    """纯 OCR — 扫描件 PDF 逐页渲染 + OCR 识别

    不走 pymupdf 文字层，直接每页渲染为 PNG 交给 OCR 引擎。
    适用于纯扫描件 / 图片型 PDF，不适用于电子版（浪费 token）。
    """

    name = "pdf-ocr"

    def __init__(self, api_key: str = "", base_url: str = ""):
        self.api_key = api_key
        self.base_url = base_url
        self.capabilities = ["ocr", "image", "pdf"]

    def execute(self, file_path: str) -> DocResult:
        import fitz
        import tempfile

        from app.core.registry import EngineRegistry

        doc = fitz.open(str(file_path))
        parts: list[str] = []
        success = 0

        # 获取 OCR 引擎实例：优先 dashscope-ocr，autofallback 关闭时不做降级
        ocr_engine = EngineRegistry.get("ocr", "dashscope-ocr")
        if ocr_engine and hasattr(ocr_engine, 'api_key') and not ocr_engine.api_key:
            if self.api_key:
                ocr_engine.api_key = self.api_key
        if ocr_engine and hasattr(ocr_engine, 'base_url') and not ocr_engine.base_url:
            if self.base_url:
                ocr_engine.base_url = self.base_url

        for i, page in enumerate(doc):
            page_num = i + 1

            # 渲染页面
            pix = page.get_pixmap(dpi=150)
            img_b64 = base64.b64encode(pix.tobytes("png")).decode()

            # OCR
            tmp_path = Path(tempfile.mktemp(suffix=f"_p{page_num}.png"))
            tmp_path.write_bytes(pix.tobytes("png"))

            ocr_text = ""
            if ocr_engine:
                try:
                    ocr_result = ocr_engine.run(str(tmp_path))
                    if ocr_result and ocr_result.text:
                        ocr_text = ocr_result.text
                        success += 1
                except Exception as e:
                    logger.warning("pdf-ocr 第 %d 页失败: %s", page_num, str(e)[:120])
                    ocr_text = f"_(OCR 失败)_"
            else:
                ocr_text = "_(OCR 引擎未注册)_"

            tmp_path.unlink(missing_ok=True)

            parts.append(
                f"## Page {page_num}\n\n"
                f"![page {page_num}](data:image/png;base64,{img_b64})\n\n"
                f"{ocr_text}"
            )

        pages = doc.page_count
        doc.close()

        logger.info("pdf-ocr: %d/%d pages success", success, pages)
        return DocResult(
            text="\n\n---\n\n".join(parts),
            pages=pages,
            engine="pdf-ocr",
            is_scanned=True,
            scanned_pages=pages,
        )


class ExcelEngine(Engine):
    """openpyxl — Excel .xlsx/.xls 转录为 Markdown 表格

    逐 sheet → 单元格值/公式 → MD table + 图表标签 + 数据摘要。
    对标 PyMuPDF/DocxEngine，但产物天然是表格而非段落。
    """

    capabilities = ["text", "table", "office"]

    def execute(self, file_path: str, **kwargs) -> DocResult:
        from openpyxl import load_workbook

        wb = load_workbook(file_path, data_only=True)
        parts: list[str] = []
        total_rows = 0

        for sn in wb.sheetnames:
            ws = wb[sn]
            rows_data: list[list[str]] = []
            for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row or 0, 1000),
                                     max_col=min(ws.max_column or 0, 50)):
                cells = []
                for cell in row:
                    val = cell.value
                    cells.append(str(val) if val is not None else "")
                rows_data.append(cells)

            if not rows_data:
                continue

            total_rows += len(rows_data) - 1  # 减表头

            # 表头：以第一行（如无则 A,B,C...）
            header = rows_data[0] if rows_data else []
            lines = ["| " + " | ".join(h if h else f"Col {i+1}" for i, h in enumerate(header)) + " |",
                     "| " + " | ".join(["---"] * len(header)) + " |"]
            for row in rows_data[1:]:
                pad = row + [""] * (len(header) - len(row))
                lines.append("| " + " | ".join(pad[:len(header)]) + " |")

            # 图表标签
            chart_labels = []
            if ws._charts:
                for ch in ws._charts:
                    try:
                        title = ch.title or ""
                        chart_labels.append(f"📊 {title}" if title else "📈 图表")
                    except Exception:
                        chart_labels.append("📈 图表")

            parts.append(
                f"## Sheet: {sn}\n\n"
                + ("\n".join(lines))
                + ("\n\n" + "\n".join(f"> {l}" for l in chart_labels) if chart_labels else "")
            )

        wb.close()
        full_text = "\n\n---\n\n".join(parts)
        logger.info(
            "ExcelEngine: %d sheets, %d rows, output %d chars",
            len(wb.sheetnames), total_rows, len(full_text),
        )
        return DocResult(
            text=full_text if full_text else f"(空工作簿: {len(wb.sheetnames)} sheets)",
            pages=len(wb.sheetnames),
            engine="openpyxl",
        )
