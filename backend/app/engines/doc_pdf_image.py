"""pdf-image 引擎 — PDF 整体转图片（渲染每页为 PNG 嵌入 markdown）

用户诉求：PDF 链路「整体作为图片转录」，后续交给 OCR / 多模态。
只针对 PDF。绑定 doc:pdf 后可替代 pypdfium2 文字提取（对纯图片/扫描 PDF 更稳）。

产物：每页一个 `![page N](page_NNNN.png)`（media_dir 写文件）或 base64 data URI。
文字为空，标记 is_scanned（纯图片 PDF）供转录预览提示；document.py 对 pdf-image
跳过自动 OCR（图片本身就是转录，避免重复渲染+OCR）。
"""

import base64
import io
import logging
from pathlib import Path

from app.core.protocols import Engine
from app.engines.doc_types import DocResult

logger = logging.getLogger(__name__)

# 渲染分辨率：PDF 默认 72dpi，×2.0 ≈ 150dpi（足够 OCR 识别，体积可控）
RENDER_SCALE = 2.0


class PdfImageEngine(Engine):
    """PDF 整体转图片 — pypdfium2 逐页渲染为 PNG"""

    name = "pdf-image"
    capabilities = ["pdf", "image"]

    def execute(self, file_path: str, **kwargs) -> DocResult:
        import pypdfium2 as pdfium

        media_dir = kwargs.get("media_dir", "")
        pdf = pdfium.PdfDocument(str(file_path))
        parts: list[str] = []

        for i in range(len(pdf)):
            page_num = i + 1
            page = pdf[i]
            bitmap = page.render(scale=RENDER_SCALE)
            pil_img = bitmap.to_pil()
            if media_dir:
                fname = f"page_{page_num:04d}.png"
                pil_img.save(str(Path(media_dir) / fname), format="PNG")
                md_ref = f"![page {page_num}]({fname})"
            else:
                buf = io.BytesIO()
                pil_img.save(buf, format="PNG")
                md_ref = f"![page {page_num}](data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()})"
            parts.append(f"## Page {page_num}\n\n{md_ref}")

        pages = len(parts)
        pdf.close()
        logger.info("pdf-image: %d pages rendered", pages)
        return DocResult(
            text="\n\n---\n\n".join(parts),
            pages=pages,
            engine="pdf-image",
            is_scanned=True,       # 纯图片 PDF（转录预览提示）
            scanned_pages=pages,
        )
