"""
文本适配器 — 最简管道（兜底）
"""

import hashlib
import logging

from app.core.types import PipelineInput, AdapterOutput, RawMetadata, InputType, SourceType, ProgressEvent
from app.core.protocols import PipelineAdapter

logger = logging.getLogger(__name__)

CHAT_PATTERNS = [
    r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\s+\S+:",   # 微信导出
    r"^\d{2}/\d{2}/\d{4}.*\d{2}:\d{2}.*:",        # 钉钉导出
]


def _detect_text_type(text: str) -> SourceType:
    """快速检测文本类型"""
    import re
    lines = text.strip().split("\n")[:10]
    sample = "\n".join(lines)
    for pattern in CHAT_PATTERNS:
        if re.search(pattern, sample, re.MULTILINE):
            return SourceType.CHAT
    # 简单推断：多行+有说话人模式 → chat
    if any(":" in l and len(l.split(":")[0].strip()) <= 8 for l in lines if l.strip()):
        return SourceType.CHAT
    return SourceType.TEXT


class TextAdapter(PipelineAdapter):
    name = "text"

    def accept(self, input: PipelineInput) -> bool:
        return input.type == InputType.TEXT

    async def adapt(self, input: PipelineInput,
                    on_progress=None,
                    engine_config: dict | None = None,
    ) -> AdapterOutput:
        if on_progress:
            on_progress(ProgressEvent(phase="preprocess", status="running"))

        text = (input.text or "").strip()
        if not text:
            raise ValueError("文本内容为空，请输入有效内容")
        source_type = _detect_text_type(text)

        if on_progress:
            on_progress(ProgressEvent(phase="preprocess", status="done"))

        return AdapterOutput(
            raw_text=text,
            metadata=RawMetadata(warnings=[]),
            engine="none",
        )
