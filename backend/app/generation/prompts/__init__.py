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
    coerce_style,
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
#
# "2"（2026-10-05）：合成骨架换成读者问题驱动五节 + 合成文风加「说人话/不限篇幅」
# 两条 + 新增 `plain`（通俗）风格档并设为全局默认。三处都会改变输出，故 bump。
#
# "3"（2026-10-05，"2" 的验收修订）："2" 首跑实测锚点 **2.5 个/段**（判据 ≤1）、
# 且产物末尾照模板写出了内部术语「完整输入」。本次收紧锚点（分歧条行末集中标一次 /
# 每节 ≤3 个 / 第 3 步不再标）+ 文风第 8 条补排版密度 + 覆盖说明改读者语言。
# 同一次验收的两处修订，合并成一个版本号——**不与 "2" 共存**，因为 "2" 的产物
# 从未对外发布（本地重跑即被替代）。
#
# "4"（2026-10-05，真实场景驱动）：新增「## 各篇速查」工具区（维度作小节、
# 节内每篇一行）。起因是「老师看论文时同时看多篇不方便」——追出的三个诉求里，
# 有两个（看不过来 / 比不了）要的是**高密度可扫的对照**，与文章区「低密度、
# 通俗」的要求相反。分区而非折中。同样是影响输出的文本变动。
#
# "5"（2026-10-05，"4" 的真实重跑验收）：材料索引表下那句补「**直接给结论，不要
# 解释你是怎么知道的**（不要出现字段名、标签名或内部标记）」——两批实跑（3 篇论文 +
# 6 篇观点文）都稳定写出了内部标签名 `source_content`，是"模型忠实执行 prompt、
# 验收却把它当错"的第 5 次复现（**改 prompt，不是改验收**）。
PROMPT_VERSION = "5"
