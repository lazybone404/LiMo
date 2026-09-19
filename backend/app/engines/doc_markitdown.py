"""
MarkItDown 引擎 — 微软开源，DOCX/PPTX/XLSX/EPUB 转 Markdown
全自动处理文字+表格+嵌入图片，输出带内嵌图片的 Markdown。
PDF 用 docling/pypdfium2，EPUB 章节提取用 epub-container（自研 EpubEngine）。
"""

import base64
import logging
import re
from pathlib import Path

from app.core.protocols import Engine
from app.engines.doc_types import DocResult

logger = logging.getLogger(__name__)


class MarkItDownEngine(Engine):
    """MarkItDown（微软开源）— DOCX / PPTX / XLSX / EPUB 转 Markdown

    全自动处理文字+表格+嵌入图片，输出带内嵌图片的 Markdown。
    PDF 用 pymupdf4llm，EPUB 章节提取用 epub-container（自研 EpubEngine）。
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
            img_idx = 0
            data_uri_pat = re.compile(r'!\[([^\]]*)\]\(data:image\/[^;]+;base64,([^)]+)\)')
            before_count = len(data_uri_pat.findall(text))
            logger.info("MarkItDown: 找到 %d 个 base64 图片", before_count)
            if before_count == 0:
                # 诊断：检查 MarkItDown 实际输出的图片格式
                img_patterns = re.findall(r'(!\[[^\]]*\](?:\([^)]+\))?|<img[^>]+src="[^"]*"[^>]*>)', text)
                if img_patterns:
                    logger.info("MarkItDown 图片格式: %d 个非 base64 引用，示例: %s",
                              len(img_patterns), img_patterns[:3])
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
                except Exception as e:
                    logger.warning("MarkItDown 图片保存失败: alt=%s err=%s", alt[:40], e)
                    return m.group(0)
            text = data_uri_pat.sub(_save_base64, text)
            logger.info("MarkItDown: 图片写入完成 %d 个", img_idx)

        return DocResult(
            text=text,
            pages=1,
            engine="markitdown",
        )
