"""
prompts 包 — 模块化 Prompt 定义

Commit 1: 风格解耦（notes_styles.py）
Commit 2: PromptComposer + XML 分节格式（common.py）
"""

from .notes_styles import (
    COMMON_NOTE_STYLES,
    VIDEO_NOTE_STYLES,
    DOC_NOTE_STYLES,
    STYLE_ALIAS,
    normalize_style,
)

# 提示词版本 —— 写进产物自证头的 `generated.promptVersion`（`generation/provenance.py`）。
#
# ⚠️ **任何会影响输出的 prompt 文本变动都要 bump**：`generation/prompt.py` 的模板串、
# `prompts/notes_styles.py` 的风格定义、`prompt.py::get_chart_format` 的图表规范。
# 不 bump 的后果是产物自称的版本与实际用的 prompt 对不上，而「凭哪版提示词生成的」
# 正是自证四条里第②条的核心。bump 纪律见 docs/prompts-inventory.md。
#
# 从 "1" 起，且**不给历史产物补**：2026-10-04 之前的产物没有头，按「无头 ≠ 损坏」
# 处理（`parse_artifact_meta` → `(None, False)`）。把历史产物一律标成 "1" 是不诚实的
# ——它们的真实版本已不可追溯。
PROMPT_VERSION = "1"
