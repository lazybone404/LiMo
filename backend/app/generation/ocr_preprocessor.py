"""
OCR 预处理 — 对文档中嵌入的图片做 OCR，文字塞回原文

仅处理 /api/files/{tx_id}/img_NNNN.png 格式的本地图片引用。
外部 URL 和 base64 data URI 不处理（前者需要下载，后者临时性）。
"""

import asyncio
import base64
import logging
import re
from pathlib import Path

from app.llm.service import LLMService

logger = logging.getLogger(__name__)

# 本地图片引用: ![alt](/api/files/{tx_id}/img_NNNN.png)
_RE_IMG_REF = re.compile(
    r'!\[([^\]]*)\]\(/api/files/([a-zA-Z0-9_-]+)/(img_\d{4}\.[a-z]+)\)'
)

OCR_PROMPT = (
    "请提取图像中的全部文字内容。"
    "如有表格，用 Markdown 表格格式输出。"
    "不要添加任何解释说明。"
)

MAX_IMAGES = 20          # 最多 OCR 的图片数
OCR_CONCURRENCY = 3      # 并发 OCR 调用数
OCR_TEMPERATURE = 0.1
OCR_MAX_TOKENS = 2048


class OCRPreprocessor:
    """对 rawText 中的本地图片引用做 OCR，文字塞回原文"""

    def __init__(self, config: dict, ocr_model_id: int):
        self._config = config
        self._ocr_model_id = ocr_model_id

    async def process(self, raw_text: str, store_root: str) -> str:
        """匹配图片引用 → 逐一 OCR → 插入原文。无匹配则直接返回。"""
        matches = _RE_IMG_REF.findall(raw_text)
        if not matches:
            return raw_text

        if len(matches) > MAX_IMAGES:
            skipped = len(matches) - MAX_IMAGES
            logger.warning("图片过多 (%d)，只 OCR 前 %d 张，跳过 %d 张",
                           len(matches), MAX_IMAGES, skipped)
            matches = matches[:MAX_IMAGES]

        # 建立 (alt, tx_id, filename) → OCR 结果的映射
        sem = asyncio.Semaphore(OCR_CONCURRENCY)
        ocr_llm: LLMService | None = None

        async def ocr_one(alt: str, tx_id: str, filename: str) -> str:
            nonlocal ocr_llm
            async with sem:
                try:
                    img_path = Path(store_root) / "files" / tx_id / filename
                    if not img_path.exists():
                        logger.warning("OCR: 图片不存在 %s", img_path)
                        return f"[图片缺失: {filename}]"

                    img_data = img_path.read_bytes()
                    ext = filename.rsplit(".", 1)[-1].lower()
                    mime = {"png": "image/png", "jpg": "image/jpeg",
                            "jpeg": "image/jpeg", "webp": "image/webp",
                            "gif": "image/gif", "bmp": "image/bmp"}.get(ext, "image/png")
                    b64 = base64.b64encode(img_data).decode()
                    data_uri = f"data:{mime};base64,{b64}"

                    # 延迟创建 LLMService（复用同一个实例）
                    if ocr_llm is None:
                        ocr_llm = LLMService(self._config, model_id=self._ocr_model_id)

                    result = await ocr_llm.call(
                        OCR_PROMPT,
                        image_urls=[data_uri],
                        max_tokens=OCR_MAX_TOKENS,
                        temperature=OCR_TEMPERATURE,
                        max_continuations=0,
                    )
                    logger.info("OCR: %s/%s (%s) → %d chars",
                                tx_id[:8], filename, alt, len(result))
                    return result.strip() or "[OCR 结果为空]"

                except Exception as e:
                    logger.warning("OCR 失败: %s/%s: %s", tx_id[:8], filename, str(e)[:120])
                    return f"[OCR 失败: {filename}]"

        # 并行 OCR 所有图片
        tasks = [ocr_one(alt, tx_id, filename)
                 for alt, tx_id, filename in matches]
        results = await asyncio.gather(*tasks)

        # 用正则替换：在原图片引用后追加 OCR 结果
        result_map = {key: text for key, text in zip(matches, results)}
        idx = [0]

        def replacer(m: re.Match) -> str:
            key = (m.group(1), m.group(2), m.group(3))
            ocr_text = result_map.get(key, "")
            if ocr_text:
                return (f"{m.group(0)}\n\n"
                        f"[图片文字]\n{ocr_text}\n[/图片文字]\n")
            return m.group(0)

        return _RE_IMG_REF.sub(replacer, raw_text)
