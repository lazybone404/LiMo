"""
分层 System Prompt 加载器 — 参照 Pi 的 AGENTS.md + APPEND_SYSTEM.md + environment.md

四层叠加：
  base.md     → 角色定义
  rules.md    → 硬约束（禁止编造、先查再答…）
  formats.md  → 回答可用的语义块（`limo-block` 卡片组 / 追问建议）
  context     → 动态注入（当前文档内容）

⚠️ `formats.md` 是**静态**层，不含 `doc_context`。对话侧与产物侧**刻意不共用同一份
规范文本**：产物那边由 `generation/prompt.py::get_markdown_block_format` 注入
（只教 `key-takeaway`），这里是对话侧的两种 type。两侧的块类型集合不相交
（见 `core/artifact_blocks.py` 的 `ARTIFACT_BLOCK_TYPES` / `CHAT_BLOCK_TYPES`），
措辞、触发条件与示例也各不相同 —— 共用一份会让某一侧被迫接受另一侧的纪律。
"""

from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent


def _read(filename: str) -> str:
    path = _PROMPTS_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


class PromptLoader:
    """组装完整的 system prompt"""

    def __init__(self):
        self.base = _read("base.md")
        self.rules = _read("rules.md")
        self.formats = _read("formats.md")

    def build(self, doc_context: str = "") -> str:
        """构建完整 system prompt。doc_context 为注入的文档摘要。

        ⚠️ `formats` 排在 `rules` 之后、`doc_context` 之前：它是「怎么把回答呈现得好读」
        的**补充**，不是硬约束（排在 rules 前会让它看起来比「禁止编造」更重要）。
        `_read` 对缺失文件返回空串，故 `"\n\n".join` 不会留下多余空行。
        """
        parts = [self.base, self.rules, self.formats]
        if doc_context:
            parts.append(f"\n--- 当前文档 ---\n{doc_context}")
        return "\n\n".join(parts)


# 模块级单例
loader = PromptLoader()
