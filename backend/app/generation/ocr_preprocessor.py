"""
OCR 预处理 — 对文档中嵌入的图片做 OCR，文字塞回原文

处理 assets/img_NNNN.png（新格式，相对路径）和旧格式 /api/files/{tx_id}/img_NNNN.png。
外部 URL 和 base64 data URI 不处理（前者需要下载，后者临时性）。
"""

import asyncio
import base64
import logging
import re
from pathlib import Path

from app.llm.service import LLMService

logger = logging.getLogger(__name__)

# 新格式: ![alt](assets/img_NNNN.png) / ![alt](assets/fig_NNNN.png)
# 前缀同时容纳 pypdfium2（img_）与 docling（fig_）引擎的图片命名
# 捕获组 3 个：alt / assets路径 / 文件名（findall 解包 (alt, arg2, filename) 依赖此结构）
#
# ⚠️ 形态与 `core/markdown_assets.ASSET_REF_RE` 相似，但**刻意更严、不是它的副本**：
#    这里限定 `(?:img|fig)_\d{4}\.[a-z]+`——只认 OCR 管线自己产出的图片名。
#    放宽成通用版会让用户正文里手写的任意 `![x](assets/任意文件)` 也进 OCR 队列。
#    全项目**只有本文件**需要这个严格度，故不合并（搬进 core 会制造「看起来同源
#    其实不同源」的错觉，模式 #61）。**新增通用 `assets/` 匹配需求请用 core 那份。**
_RE_IMG_REF = re.compile(
    r'!\[([^\]]*)\]\((assets/((?:img|fig)_\d{4}\.[a-z]+))\)'
)
# 旧格式兼容
_RE_IMG_REF_OLD = re.compile(
    r'!\[([^\]]*)\]\(/api/files/([a-zA-Z0-9_-]+)/((?:img|fig)_\d{4}\.[a-z]+)\)'
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

    async def process(self, raw_text: str, store_root: str, doc_dir: str = "") -> str:
        """匹配图片引用 → 逐一 OCR → 插入原文。支持新旧两种路径格式。"""
        new_matches = _RE_IMG_REF.findall(raw_text)       # (alt, "assets/img_NNNN.png", "img_NNNN.png")
        old_matches = _RE_IMG_REF_OLD.findall(raw_text)    # (alt, tx_id, filename)

        all_matches = new_matches + old_matches
        if not all_matches:
            return raw_text

        if len(all_matches) > MAX_IMAGES:
            skipped = len(all_matches) - MAX_IMAGES
            logger.warning("图片过多 (%d)，只 OCR 前 %d 张，跳过 %d 张",
                           len(all_matches), MAX_IMAGES, skipped)
            all_matches = all_matches[:MAX_IMAGES]

        sem = asyncio.Semaphore(OCR_CONCURRENCY)
        ocr_llm: LLMService | None = None

        async def ocr_one(alt: str, arg2: str, filename: str) -> str:
            nonlocal ocr_llm
            async with sem:
                try:
                    # arg2 is either full asset path ("assets/img_NNNN.png") or tx_id
                    if doc_dir and arg2.startswith("assets/"):
                        img_path = Path(doc_dir) / arg2
                    else:
                        img_path = Path(store_root) / "files" / arg2 / filename
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

                    if ocr_llm is None:
                        ocr_llm = LLMService(self._config, model_id=self._ocr_model_id)

                    result = await ocr_llm.call(
                        OCR_PROMPT,
                        image_urls=[data_uri],
                        max_tokens=OCR_MAX_TOKENS,
                        temperature=OCR_TEMPERATURE,
                        max_continuations=0,
                    )
                    logger.info("OCR: %s (%s) → %d chars",
                                filename, alt, len(result))
                    return result.strip() or "[OCR 结果为空]"

                except Exception as e:
                    logger.warning("OCR 失败: %s: %s", filename, str(e)[:120])
                    return f"[OCR 失败: {filename}]"

        # 并行 OCR 所有图片
        tasks = [ocr_one(alt, arg2, filename)
                 for alt, arg2, filename in all_matches]
        results = await asyncio.gather(*tasks)

        # 替换：新格式
        result_map_new = {key: text for key, text in zip(new_matches, results[:len(new_matches)])}
        result_map_old = {key: text for key, text in zip(old_matches, results[len(new_matches):])}

        def replacer_new(m: re.Match) -> str:
            key = (m.group(1), m.group(2), m.group(3))
            ocr_text = result_map_new.get(key, "")
            if ocr_text:
                return f"{m.group(0)}\n\n[图片文字]\n{ocr_text}\n[/图片文字]\n"
            return m.group(0)

        def replacer_old(m: re.Match) -> str:
            key = (m.group(1), m.group(2), m.group(3))
            ocr_text = result_map_old.get(key, "")
            if ocr_text:
                return f"{m.group(0)}\n\n[图片文字]\n{ocr_text}\n[/图片文字]\n"
            return m.group(0)

        raw_text = _RE_IMG_REF.sub(replacer_new, raw_text)
        raw_text = _RE_IMG_REF_OLD.sub(replacer_old, raw_text)
        return raw_text
