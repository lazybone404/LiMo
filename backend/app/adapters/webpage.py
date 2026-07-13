"""
网页适配器 — URL → trafilatura 正文提取
"""

import logging
import re

import trafilatura

from app.core.types import PipelineInput, AdapterOutput, RawMetadata, InputType, SourceType, ProgressEvent
from app.core.protocols import PipelineAdapter
from app.core.errors import TranscribeError

logger = logging.getLogger(__name__)


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

        # 下载
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            raise TranscribeError.empty(detail=f"无法下载: {url}")

        if on_progress:
            on_progress(ProgressEvent(phase="extract", status="running", message="提取正文..."))

        # 提取
        extracted = trafilatura.extract(
            downloaded,
            output_format="markdown",
            include_comments=False,
            include_tables=True,
            target_language="zh",
            deduplicate=True,
        )
        if not extracted:
            raise TranscribeError.empty(detail=f"正文提取为空: {url}")

        # 后处理：过滤 base64/CSS/控制字符等垃圾
        extracted = _clean_extracted(extracted)
        if not extracted or len(extracted) < 10:
            raise TranscribeError.empty(detail=f"正文提取后为空: {url}")

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
