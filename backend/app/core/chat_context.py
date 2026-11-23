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

# 文档上下文缓存（C5）：docId:limit → (contentHash, text)，内容变则失效，超上限防膨胀
_doc_ctx_cache: dict[str, tuple[str, str]] = {}
_DOC_CACHE_MAX = 200


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


def _pick_doc_text(tx: dict, part: str, reader) -> tuple[str, str]:
    """这一轮注入哪一份正文。返回 `(正文, 标签)`；标签为空表示就是逐字稿。

    ⚠️ 读不到指定的那份**就退回逐字稿**，不抛错：注入层失败不该让整轮对话失败，
    而模型看到的是逐字稿 —— 与它「什么都没看」相差一个数量级。
    """
    if part and part != "transcript" and reader is not None:
        try:
            text = reader(tx.get("id", ""), part)
        except Exception as e:  # noqa: BLE001 —— 任何失败都退回逐字稿
            logger.warning("注入产物读取失败，退回逐字稿: %s", str(e)[:120])
            text = ""
        if text:
            return text, part
    return tx.get("rawText", "") or "", ""


def _compose_digest(title: str, full: str, limit: int, query: str,
                    can_read: bool) -> str:
    """装不下时给「目录 + 相关片段」而不是只截开头。

    ⚠️ 只截开头时，模型会以为「后面没有了」；目录告诉它**有哪些内容**，片段告诉它
    **答案可能在哪**。两者加起来仍受 `limit` 约束（片段窗口按剩余额度分）。
    """
    from app.services.markdown_sections import outline
    from app.core.text_utils import find_passages

    toc = outline(full)
    window = max(300, min(800, limit // 3))
    passages = find_passages(full, query, window=window, max_parts=3) if query.strip() else []

    parts: list[str] = [f"（这一篇共 {len(full)} 字。）"]
    # 开头总是给一段：目录是地图，但「这一篇在讲什么」还是要读几句正文才知道。
    # ⚠️ 没有读取工具时（服务端检索那一轮）**必须**给，否则模型只有一张目录 ——
    # 它既读不到正文，也没法调工具。
    head_budget = max(0, min(900, limit // 2))
    head_text = smart_truncate(full, head_budget) if head_budget > 100 else ""
    if head_text:
        parts.append("开头：")
        parts.append(head_text)
    if toc:
        parts.append("目录：")
        parts.extend(f"- {s['title']}" for s in toc)
    if passages:
        parts.append("与问题相关的片段：")
        for start, end in passages:
            body = smart_truncate(full[start:end], window)
            parts.append(f"（第 {start}–{end} 字）\n{body}")

    # 续读的起点要给**具体的数**（照 pi 的 `read(offset)`）：只说「可以再读」时，
    # 模型得自己猜从哪开始；给了 nextOffset，它就只差一次调用。
    if can_read:
        nxt = max(0, len(head_text) - 3)   # smart_truncate 末尾追加的是 "..."
        tail = ("其余内容没有进入上下文；要看某一节可以用 lookup_doc 传 section，"
                f"要按顺序往后读传 offset={nxt}。" if toc else
                f"其余内容没有进入上下文；要往后读可以用 lookup_doc 传 offset={nxt}，"
                "或者在文档内用 query 检索。")
    else:
        tail = "其余内容没有进入上下文，本轮没有读取工具。"
    parts.append(tail)
    return "\n".join(parts)


def build_doc_context(store: object, doc_ids: list[str], budget: int,
                      doc_mode: str = "auto", *, query: str = "",
                      part: str = "", reader=None,
                      can_read: bool = True) -> tuple[str, int]:
    """构建文档引用上下文，按预算富余程度分级截断。

    - doc_mode='full' → 放宽到 DOC_FULL_LIMIT
    - doc_mode='summary' → DOC_SUMMARY_LIMIT
    - doc_mode='auto' → budget ≥ RICH_BUDGET 时 full，否则 summary

    `part` 决定注入哪一份（笔记 / 整理版 / 逐字稿，空 = 逐字稿），`reader` 是取产物
    正文的回调 `(doc_id, part) -> str | None`。`query` 是这一轮的问题，装不下时用来
    挑相关片段。`can_read=False`（服务端检索那一轮没有本地工具）时，说明里不提工具。

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
        full, label = _pick_doc_text(tx, part, reader)
        title = tx.get("title", did)
        head = f"[{title}{' · ' + label if label else ''}]"

        if len(full) <= limit:
            text = full
        else:
            # 有目录或能按问题挑出片段时走「摘要形态」；否则退回纯截断（带缓存）。
            from app.services.markdown_sections import outline
            digest = bool(outline(full)) or bool(query.strip())
            if digest:
                text = _compose_digest(title, full, limit, query, can_read)
            else:
                ck = f"{did}:{limit}"
                h = str(tx.get("contentHash") or tx.get("createdAt") or "")
                cached = _doc_ctx_cache.get(ck)
                if cached and cached[0] == h:
                    text = cached[1]
                else:
                    text = smart_truncate(full, limit)
                    if len(_doc_ctx_cache) >= _DOC_CACHE_MAX:
                        _doc_ctx_cache.clear()
                    _doc_ctx_cache[ck] = (h, text)
                # ⚠️ **截断必须告诉模型**（借鉴 pi 的 `truncate.ts`：闸门装在层里，
                # 不指望模型自觉）。只给省略号时，模型会把「只看到前两千字」当成
                # 「文档里没有这段」，然后断言「原文没提到」。
                # ⚠️ 说明接在缓存文本之后、**不进缓存**：缓存键是 `docId:limit`，
                # 而字数随时可变，混进去会让缓存失效判据变成两件事。
                shown = max(0, len(text) - 3)  # 去掉 smart_truncate 追加的 "..."
                text = (f"{text}\n\n（这一篇共 {len(full)} 字，以上只注入了开头 {shown} 字，"
                        f"其余部分没有进入上下文。）")
        parts.append(f"{head}\n{text}")
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
