"""
PyMuPDF 引擎插件 — AGPL-3.0 许可证，用户自行承担协议风险。

安装:
  pip install pymupdf
  复制此文件到 ~/.limo/plugins/

LiMo 启动时自动发现并注册为 doc 引擎。
"""

import base64
import logging
from pathlib import Path

from app.engines.plugin_loader import EnginePlugin

logger = logging.getLogger(__name__)


class PyMuPDFPlugin(EnginePlugin):
    name = "pymupdf"
    category = "doc"
    license = "AGPL-3.0"
    description = ("PyMuPDF 高精度 PDF 解析 — 文字层提取 + 表格识别 + 嵌入图片\n"
                   "⚠️ AGPL v3 许可证，商用需获取授权")
    file_types = [".pdf"]

    def process(self, file_path: str, config: dict) -> str:
        """从 PDF 提取文字 + 表格，返回 Markdown"""
        try:
            import pymupdf as fitz
        except ImportError:
            import fitz

        doc = fitz.Document(str(file_path))
        parts: list[str] = []
        scanned_pages = 0
        img_idx = 0
        media_dir = config.get("media_dir", "")

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

            # 2. 表格识别
            try:
                tabs = page.find_tables()
                for t in tabs:
                    md_table = t.to_markdown()
                    if md_table.strip():
                        page_parts.append("\n" + md_table.strip())
            except Exception as e:
                logger.warning("表格提取失败 p%d: %s", page_num, str(e)[:80])

            # 3. 嵌入图片
            try:
                images = page.get_images(full=True)
                for img in images:
                    xref = img[0]
                    base = doc.extract_image(xref)
                    ext = (base.get("ext") or "png").lower()
                    if ext in ("png", "jpeg", "jpg"):
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
            "PyMuPDF plugin: %d pages, %d scanned, is_scanned=%s, output %d chars",
            pages, scanned_pages, is_scanned, len(full_text),
        )
        return full_text
