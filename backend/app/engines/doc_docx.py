"""
python-docx 引擎 — Word .docx 转录为 Markdown

对标 PyMuPDF：文字层 + 表格 + 内嵌图片 → 富 MD。
按文档流顺序遍历 body 元素，不区分页面。

text_only=True → 跳过图片 base64 编码，仅输出文字 + 表格。
"""

import base64
import logging
import re
from pathlib import Path

from app.core.protocols import Engine
from app.engines.doc_types import DocResult

logger = logging.getLogger(__name__)


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
        # 简单处理：反引号内的内容不转义
        return text.replace("\\", "\\\\")
