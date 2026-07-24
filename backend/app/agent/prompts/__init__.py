"""
分层 System Prompt 加载器 — 参照 Pi 的 AGENTS.md + APPEND_SYSTEM.md + environment.md

三层叠加：
  base.md   → 角色定义
  rules.md  → 硬约束（禁止编造、先查再答…）
  context   → 动态注入（当前文档内容）
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

    def build(self, doc_context: str = "") -> str:
        """构建完整 system prompt。doc_context 为注入的文档摘要。"""
        parts = [self.base, self.rules]
        if doc_context:
            parts.append(f"\n--- 当前文档 ---\n{doc_context}")
        return "\n\n".join(parts)


# 模块级单例
loader = PromptLoader()
