"""按字符窗口切分长正文 —— `continuation` 形状的**唯一一份**。

两个宿主共用：

- `services/query.py::QueryService._build`（产物读取，走 `get_artifact`）
- `limo_cli/mcp_tools.py::_read_raw`（文档原文读取，走 `get_resource`）

两处此前各写一遍，**逐字同形却互不相认**：`_build` 将来加一个字段或改一句 hint，
MCP 那份不会跟着变，同一个「还剩多少字 / 下次从哪读」在两个入口给出不同形状，
而两边都不报错（模式 #54 的典型入口）。

⚠️ **放 `core/` 不放 `services/`**：MCP 的 `--http` 模式只为切一段窗口，不值得
拉起整个 `query.py`（连带 store / search 的 import 链）。本模块零依赖。
"""

from __future__ import annotations


def window_text(text: str, offset: int = 0, max_chars: int | None = None
                ) -> tuple[int, str, dict | None]:
    """切出 `[offset, offset+max_chars)`，返回 `(起点, 窗口文本, continuation 或 None)`。

    **起点是夹紧后的**（负数当 0），调用方要拿它回显 `offset` 字段。自己再算一遍
    `max(0, …)` 就是第二份夹紧判据 —— 而两份的失效形态是「回显说 -5，实际从 0 读」，
    结构完整、值不对。

    `max_chars is None` = **不截断**（不是「用某个默认值」—— 那个默认值住在
    `QueryService.DEFAULT_MAX_CHARS`，本函数不认识它）。

    `continuation` 为 `None` 就是「读完了」，`truncated` 由它派生，不另设字段 ——
    两个值是同一件事，分开存迟早会互相矛盾。
    """
    total = len(text)
    start = max(0, int(offset or 0))
    window = text[start:]
    if max_chars is None or len(window) <= max_chars:
        return start, window, None
    window = window[:max_chars]
    next_offset = start + len(window)
    return start, window, {
        "reason": "char_limit",
        "limit": max_chars,
        "next_offset": next_offset,
        "remaining_chars": total - next_offset,
        "hint": (f"续读：offset={next_offset}"
                 f"（或调大 max_chars 一次取全，全文 {total} 字符）"),
    }
