"""对话上下文预算引擎 — token 感知的历史裁剪 + 文档引用分级

依据 pi-docs/对话上下文管理计划.md C2。
budget = context_window × ratio（默认 0.7，留 30% 输出空间）
分配：system prompt → 文档引用 → 历史（反向裁剪到剩余预算）。
修正 NextChat 缺陷：其 max_tokens 同时当输入预算和输出上限，本引擎分开。
"""

import logging

from app.core.token import estimate_tokens
from app.core.model_resolver import resolve_context_window
from app.core.text_utils import smart_truncate

logger = logging.getLogger(__name__)

DEFAULT_BUDGET_RATIO = 0.7        # 上下文窗口用于输入的比例（留输出）
DOC_FULL_LIMIT = 2000             # 预算富余时文档引用放宽截断（字符）
DOC_SUMMARY_LIMIT = 1000          # 预算紧张时文档引用截断（字符）
RICH_BUDGET = 200_000             # 视为「预算富余」的阈值（token）
DEFAULT_HISTORY_LIMIT = 0         # 0 = 不限制，按预算自动


def compute_budget(config: dict, model_id: int | None) -> tuple[int, int]:
    """返回 (context_window, budget)。budget = context_window × ratio。

    ratio 来自 chat_context.budget_ratio（auto → 0.7；数字 → 直接使用）。
    """
    window = resolve_context_window(config, model_id)
    cc = config.get("chat_context") or {}
    ratio = DEFAULT_BUDGET_RATIO
    br = cc.get("budget_ratio", "auto")
    if br not in (None, "auto"):
        try:
            ratio = float(br)
        except (TypeError, ValueError):
            logger.warning("chat_context.budget_ratio 非法: %r，回退 0.7", br)
    ratio = max(0.3, min(0.95, ratio))  # 钳制到合理范围
    return window, int(window * ratio)


def build_doc_context(store: object, doc_ids: list[str], budget: int,
                      doc_mode: str = "auto") -> tuple[str, int]:
    """构建文档引用上下文，按预算富余程度分级截断。

    - doc_mode='full' → 放宽到 DOC_FULL_LIMIT
    - doc_mode='summary' → DOC_SUMMARY_LIMIT
    - doc_mode='auto' → budget ≥ RICH_BUDGET 时 full，否则 summary

    返回 (context_text, tokens)。
    """
    if not doc_ids:
        return "（未选中文档，可用 list_docs 查看可用文档）", 0
    rich = budget >= RICH_BUDGET
    mode = "full" if (doc_mode == "full" or (doc_mode == "auto" and rich)) else "summary"
    limit = DOC_FULL_LIMIT if mode == "full" else DOC_SUMMARY_LIMIT

    parts: list[str] = []
    tokens = 0
    for did in doc_ids:
        tx = store.get_transcript(did)
        if not tx:
            continue
        text = smart_truncate(tx.get("rawText", ""), limit)
        parts.append(f"[{tx.get('title', did)}]\n{text}")
        tokens += estimate_tokens(text)
    return ("\n\n".join(parts) if parts else "（未选中文档）"), tokens


def trim_history(messages: list[dict], budget: int,
                 history_limit: int = DEFAULT_HISTORY_LIMIT) -> list[dict]:
    """按 token 预算反向裁剪历史（从最新往前，累计达预算即截断）。

    跳过 error 消息（isError / role='system' 不进上下文，对齐 NextChat）。
    history_limit > 0 时先按条数截断（C4 用户覆盖）。
    """
    if not messages:
        return []
    msgs = messages
    if history_limit and history_limit > 0:
        msgs = msgs[-history_limit:]

    total = 0
    kept: list[dict] = []
    for m in reversed(msgs):
        if m.get("role") == "system" or m.get("isError"):
            continue
        t = estimate_tokens(m.get("content", ""))
        if total + t > budget and kept:
            break
        total += t
        kept.append(m)
    kept.reverse()
    if len(kept) < len(msgs):
        logger.info("历史裁剪 %d→%d 条（预算 %d token）", len(msgs), len(kept), budget)
    return kept
