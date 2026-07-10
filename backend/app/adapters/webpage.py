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
        )
        if not extracted:
            raise TranscribeError.empty(detail=f"正文提取为空: {url}")

        # 元数据
        meta = trafilatura.extract_metadata(downloaded)

        if on_progress:
            on_progress(ProgressEvent(phase="extract", status="done"))

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
