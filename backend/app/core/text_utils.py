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


def split_into_chunks(
    text: str, max_chars: int = 8000, overlap: int = 200,
) -> list[str]:
    """将长文本按句子/段落边界切分为重叠块。

    切片边界优先级（复用 smart_truncate 的 _SENTENCE_BOUNDARIES）：
    中文句号+换行 > 中文句号 > 英文句号+空格 > 空行 > 单换行。
    每块最多 max_chars，相邻块重叠 overlap 字符防止跨边界信息丢失。
    找不到合适边界时硬切。
    """
    if len(text) <= max_chars:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end >= len(text):
            chunks.append(text[start:])
            break

        # 在 [end - max_chars*0.3, end] 中找最佳边界
        search_start = max(start, end - int(max_chars * 0.3))
        chunk_text = text[search_start:end]
        best = -1
        for boundary in _SENTENCE_BOUNDARIES:
            idx = chunk_text.rfind(boundary)
            if idx >= 0:
                best = search_start + idx + len(boundary)
                break
        if best < 0:
            best = end  # 硬切

        chunks.append(text[start:best])
        start = max(best - overlap, start + 1)  # 重叠 + 前进保护

    return chunks


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


def sanitize_reference_name(name: str | None, max_len: int = 80) -> str:
    """结构标记名称清洗——把不可信的名称安全嵌进结构标记。

    当前消费方两处：参考文件的 `来源="{name}"`（prompt.py）与合成文档的出处列表
    （transcript_meta.py）。名称来自用户（上传文件名 / 文档标题），其中的换行可以
    伪造成新的一行结构标记，双引号可以闭合属性引号，全角方括号可以让名字里出现与
    结构标记逐字相同的串。这里只做**结构安全**处理，不做内容审查——内容本就是用户
    自己的素材，语义层由 prompt 约束负责。

    ⚠️ 本函数只防**名称**这一层。正文级注入（正文里写「忽略以上指令」）它完全无感，
    不能视为安全边界。
    """
    n = " ".join((name or "").split())     # 换行/制表/连续空白 → 单空格
    n = n.replace('"', "'")
    n = n.replace('【', '[').replace('】', ']')   # 不与结构标记【参考资料 N】/【文档 i】共用字符
    return n[:max_len] or "未命名"
