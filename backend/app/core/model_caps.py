"""模型能力位判据 — 单一真相源（P1-15）。

`capabilities` 是模型元数据里的字符串列表（`["text", "image", "video", "document"]`，
用户在设置页可手工编辑）。「这个模型认不认 X」的判据此前在 **6 处** 各写一遍同构的
`any(bit in (c or "") for c in caps)`：

    llm/service.py:117                   supports_vision     "image"
    llm/service.py:127                   supports_document   "document"
    llm/service.py:134                   supports_reasoning  "reasoning"
    generation/content_preparer.py:133   视频直传开关         "video"
    generation/content_preparer.py:198   图片发给 LLM        "image"
    generation/content_preparer.py:308   文档直传开关         "document"

⚠️ **为什么必须收敛**（模式 #54「同源是搜索题不是记忆题」）：
`service.py` 的 `supports_document` 与 `content_preparer.py:308` 的 gate 是
**同一件事的两份判据**，而 `supports_document` **全仓零调用点** —— 两份判据里
有一份是死的，改哪一份都对不上另一份。任一处日后放宽（例如允许 `"pdf_input"`
也算文档能力），另一处会**静默地继续按老判据工作**，且零报错。

⚠️ 语义**逐值等价**于收敛前的内联写法，不是重新设计：
  - 判据是**子串包含**（`bit in c`）而非相等 —— `capabilities` 是自由字符串，
    历史与未来都有 `image` / `image_input` 这类变体
  - 保留 `(c or "")`：列表里混进 `None`/空串（手编 YAML 写出 `- ` 就是 `None`）
    时不抛 `TypeError`，与既有行为一致
  - **刻意不做** `capabilities or []` 兜底：调用方一律用
    `cfg.get("capabilities", [])` 的形态传列表；把 `None` 悄悄当空列表会掩盖
    一个本该暴露的类型错误（「失败显式化」原则）

⚠️ 新增消费方时**只调本函数**，不要复制判据表达式 ——
`tests/test_model_caps.py` 的 AST 守卫会扫全仓抓内联形态（模式 #54 的守卫形态，
像 `url_utils.api_root` 那样）。
"""

from __future__ import annotations

from typing import Iterable


def has_capability(capabilities: Iterable[str], bit: str) -> bool:
    """`capabilities` 是否含能力位 `bit`（子串包含，非相等）。

    用法::

        from app.core.model_caps import has_capability

        has_capability(self.capabilities, "image")                  # LLMService 属性
        has_capability(self.llm_config.get("capabilities", []), "document")
    """
    return any(bit in (c or "") for c in capabilities)
