"""
网页适配器 — URL → 正文提取（trafilatura 主 + html2text 回退）
"""

import asyncio
import logging
import re

import trafilatura

from app.core.types import PipelineInput, AdapterOutput, RawMetadata, InputType, SourceType, ProgressEvent
from app.core.protocols import PipelineAdapter
from app.core.errors import TranscribeError

logger = logging.getLogger(__name__)


def _garbage_ratio(text: str) -> float:
    """估算文本中垃圾字符（控制字符/不可打印字符）的比例"""
    if not text:
        return 1.0
    # 控制字符 + 替换字符 U+FFFD
    garbage = sum(1 for c in text if ord(c) < 32 and c not in "\n\r\t")
    # 连续 base64 块（超过 60 个 base64 字符 = 约 45 字节的二进制数据）
    b64_blocks = len(re.findall(r"[A-Za-z0-9+/=]{60,}", text))
    return (garbage + b64_blocks * 60) / max(len(text), 1)


def _extract_with_trafilatura(html: str) -> str | None:
    """trafilatura 提取，过滤垃圾"""
    text = trafilatura.extract(
        html,
        output_format="markdown",
        include_comments=False,
        include_tables=True,
        target_language="zh",
        deduplicate=True,
    )
    if not text:
        return None
    text = _clean_extracted(text)
    if _garbage_ratio(text) > 0.3:
        logger.warning("trafilatura 提取结果垃圾比例过高(%.1f%%)，尝试回退", _garbage_ratio(text) * 100)
        return None
    return text


def _extract_fallback(html: str) -> str:
    """回退方案：html2text → 纯文本提取（兜底，宁可少不可乱）"""
    try:
        import html2text
        h = html2text.HTML2Text()
        h.ignore_links = True
        h.ignore_images = True
        h.ignore_emphasis = True
        h.body_width = 0
        text = h.handle(html)
        text = _clean_extracted(text)
        if text and len(text) >= 10:
            return text
    except ImportError:
        pass

    # 终极回退：正则去标签
    text = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    text = _clean_extracted(text)
    return text.strip()


def _clean_extracted(text: str) -> str:
    """网页正文后处理：移除 CSS/JS/base64 残留、控制字符、纯符号行。

    trafilatura 对某些页面（尤其是含大量内联样式的 SPA 页面）
    会混入 base64 编码的字体/图片数据和控制字符，导致预览乱码。
    """
    import unicodedata

    lines: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue

        # 1. 跳过纯 base64/CSS 行（>60% 非 ASCII 且不含中文/日文）
        non_ascii = sum(1 for c in stripped if ord(c) > 127)
        cjk = sum(1 for c in stripped if "一" <= c <= "鿿" or "぀" <= c <= "ヿ")
        if non_ascii > len(stripped) * 0.6 and cjk == 0:
            continue

        # 2. 跳过 CSS/JS 残留行
        if re.match(r"^[{}@;:#.()\[\]\-]", stripped) and len(stripped) < 200:
            continue

        # 3. 移除行内 base64 data URI（保留前后的正常文本）
        cleaned = re.sub(r"data:[^;\"')\s]*;base64,[A-Za-z0-9+/=]{100,}", "[图片]", stripped)

        # 4. 移除控制字符
        cleaned = "".join(c for c in cleaned if ord(c) >= 32 or c in "\n\t")

        # 5. 规范化 Unicode
        cleaned = unicodedata.normalize("NFKC", cleaned)

        if cleaned.strip():
            lines.append(cleaned)

    # 合并连续空行
    result = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return result.strip()


class WebpageAdapter(PipelineAdapter):
    name = "webpage"

    def accept(self, input: PipelineInput) -> bool:
        return input.type == InputType.URL and bool(
            re.match(r"^https?://", input.url or "")
        )

    async def adapt(self, input: PipelineInput,
                    on_progress=None,
                    engine_config: dict | None = None,
    ) -> AdapterOutput:
        url = input.url or ""

        if on_progress:
            on_progress(ProgressEvent(phase="download", status="running", message="下载网页..."))

        # SSRF 防护
        from app.utils.url_security import validate_url
        validate_url(url)

        # 下载（to_thread 释放事件循环，否则 requests 最长 30s 阻塞整个后端）
        # 使用 requests 而非 trafilatura.fetch_url，后者编码处理不稳定
        import requests as _requests
        def _download() -> str:  # 同步函数（asyncio.to_thread 期望同步；async def 会返回未 await 的 coroutine）
            try:
                resp = _requests.get(
                    url,
                    timeout=30,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "zh-CN,zh;q=0.9",
                        "Accept-Encoding": "gzip, deflate",
                    },
                )
                resp.raise_for_status()
                # 优先使用响应头声明的编码，回退到 UTF-8
                resp.encoding = resp.apparent_encoding or "utf-8"
                return resp.text
            except Exception as e:
                # 回退到 trafilatura.fetch_url
                logger.warning("requests 下载失败 (%s)，回退到 trafilatura.fetch_url", str(e)[:80])
                return trafilatura.fetch_url(url)
        downloaded = await asyncio.to_thread(_download)

        if not downloaded:
            raise TranscribeError.empty(detail=f"无法下载: {url}")

        if on_progress:
            on_progress(ProgressEvent(phase="extract", status="running", message="提取正文..."))

        # 提取：trafilatura 主 → html2text 回退 → 正则去标签兜底
        extracted = _extract_with_trafilatura(downloaded)
        if not extracted:
            logger.info("trafilatura 失败，尝试 html2text 回退")
            extracted = _extract_fallback(downloaded)
        if not extracted or len(extracted) < 10:
            raise TranscribeError.empty(detail=f"正文提取为空: {url}")

        if on_progress:
            on_progress(ProgressEvent(phase="extract", status="done"))

        # 元数据
        meta = trafilatura.extract_metadata(downloaded)

        # 提取网页标题（<title> 标签内容）
        source_title = ""
        if meta and getattr(meta, "title", ""):
            source_title = str(meta.title).strip()[:120]

        return AdapterOutput(
            raw_text=extracted,
            metadata=RawMetadata(
                language="zh" if any("一" <= c <= "鿿" for c in extracted[:500]) else "en",
                source_title=source_title,
            ),
            engine="trafilatura",
        )
