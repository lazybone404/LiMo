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
