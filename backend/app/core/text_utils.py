"""
文本处理工具 — 智能截断等通用操作
"""

_SENTENCE_BOUNDARIES = (
    "。\n",
    "。\n\n",
    "。",
    ".\n",
    ". ",
    "\n\n",
    "\n",
)


def smart_truncate(text: str | None, max_chars: int,
                   ellipsis: str = "...") -> str:
    """在 max_chars 内的句子/段落边界处截断。

    边界优先级：中文句号 > 英文句号+空格 > 段落空行 > 单换行。
    不使用空格作为边界——代码/公式/英文技术文档中空格太常见。
    找不到合适边界时硬截+标记。
    """
    if not text:
        return ""
    if len(text) <= max_chars:
        return text

    truncated = text[:max_chars]
    for boundary in _SENTENCE_BOUNDARIES:
        idx = truncated.rfind(boundary)
        if idx >= max_chars * 0.4:
            return truncated[:idx + len(boundary)] + ellipsis

    return truncated + ellipsis
