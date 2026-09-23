"""对话历史压缩 — 超阈值 LLM 摘要早期消息（借鉴 Pi / NextChat summarizeSession）

触发：未压缩历史（_compress_start 之后）估算 token > 预算 × COMPRESS_RATIO
压缩模型 fallback（必补）：deepseek-flash 可用 → 用之；否则当前对话模型；调用失败 → 跳过本轮（不阻塞对话）
clear_context_at 交互：该索引前的消息既不携带也不参与压缩（基准重置）
"""

import logging
from datetime import datetime

from app.core.token import estimate_tokens
from app.llm import LLMService

logger = logging.getLogger(__name__)

COMPRESS_RATIO = 0.6    # 触发阈值：未压缩历史占预算比例
KEEP_RATIO = 0.3        # 保留最近完整消息占预算比例
MIN_TRIGGER = 1000      # 最小触发 token（防极短历史误触发）

SUMMARY_PROMPT = """请把以下早期对话压缩成一段摘要，保留：已得出的结论、用户的要求/约定、讨论过的关键点、引用过的文档。用简洁中文，200 字以内。

<对话>
{conversation}
</对话>"""


def _compress_start(conv: dict) -> int:
    """压缩基准：last_compress_index 与 clear_context_at 的较大者（clear 后重置）"""
    return max(conv.get("last_compress_index", 0), conv.get("clear_context_at", 0))


def should_compress(conv: dict, budget: int) -> bool:
    """未压缩历史估算 token 超过预算×COMPRESS_RATIO 时触发压缩"""
    messages = conv.get("messages", [])
    start = _compress_start(conv)
    if start >= len(messages):
        return False
    total = sum(estimate_tokens(m.get("content", "")) for m in messages[start:])
    return total > max(MIN_TRIGGER, int(budget * COMPRESS_RATIO))


def _plan_keep_start(messages: list[dict], start: int, budget: int) -> int:
    """保留最近 KEEP_RATIO×budget token 的完整消息，更早的（start 起）压缩。返回 keep_start。"""
    if start >= len(messages):
        return start
    keep_budget = max(200, int(budget * KEEP_RATIO))
    total = 0
    keep_start = len(messages)
    for i in range(len(messages) - 1, start - 1, -1):
        t = estimate_tokens(messages[i].get("content", ""))
        if total + t > keep_budget:
            keep_start = i + 1
            break
        total += t
        keep_start = i
    return max(keep_start, start)


def _build_compress_llm(config: dict, model_id: int | None) -> LLMService | None:
    """压缩模型 fallback：deepseek-flash（便宜）→ 当前对话模型 → None（跳过本轮）

    ⚠️ 一级 fallback 原写死 deepseek-chat，而该模型名官方已于 **2026-07-24 停用**——
    停用后这里每次构造都会失败并静默掉到二级「当前对话模型」，一级 fallback 名存实亡
    （功能上被二级兜住，所以一直没暴露）。2026-09-23 改为 deepseek-flash（V4.1-Flash，
    官方现役里最便宜的一档）。
    """
    # 1. deepseek-flash（deepseek provider 有 key 时）
    from app.routers.model import _get_providers
    ds = next((p for p in _get_providers(config)
               if p["id"] == "deepseek" and p.get("api_key")), None)
    if ds:
        compress_cfg = {
            "llm": {
                "provider": "deepseek",
                "api_key": ds["api_key"],
                "model": {"deepseek": "deepseek-flash"},
                "base_url": {"deepseek": ds.get("base_url") or "https://api.deepseek.com"},
            }
        }
        try:
            return LLMService(compress_cfg)
        except Exception:
            logger.warning("deepseek-flash 压缩模型构造失败，回退当前对话模型")
    # 2. 当前对话模型
    if model_id:
        try:
            return LLMService(config, model_id=model_id)
        except Exception:
            logger.warning("当前对话模型不可用于压缩，跳过本轮")
            return None
    return None


async def compress_history(config: dict, store, conv: dict,
                           model_id: int | None, budget: int) -> tuple[str | None, int]:
    """压缩早期消息追加到 summary。返回 (new_summary, compressed_count)；跳过返回 (None, 0)。"""
    messages = conv.get("messages", [])
    start = _compress_start(conv)
    keep_start = _plan_keep_start(messages, start, budget)
    to_compress = messages[start:keep_start]
    if not to_compress:
        return None, 0

    llm = _build_compress_llm(config, model_id)
    if llm is None:
        return None, 0
    try:
        conversation_text = "\n".join(
            f"[{m.get('role')}]\n{m.get('content', '')}" for m in to_compress)
        resp = await llm.client.chat.completions.create(
            model=llm.model,
            messages=[{"role": "user", "content": SUMMARY_PROMPT.format(conversation=conversation_text)}],
            temperature=0.3,
        )
        summary_text = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        # 压缩失败不阻塞对话（评审点 1：明确降级）
        logger.warning("历史压缩调用失败，跳过本轮: %s", str(e)[:120])
        return None, 0
    finally:
        try:
            await llm.aclose()
        except Exception:
            pass

    if not summary_text:
        return None, 0
    old = conv.get("summary") or ""
    conv["summary"] = f"{old}\n\n{summary_text}" if old else summary_text
    conv["last_compress_index"] = keep_start
    conv["updated_at"] = datetime.now().isoformat()
    store.save_conversation(conv)
    logger.info("历史压缩 %d 条 → summary %d 字", len(to_compress), len(conv["summary"]))
    return conv["summary"], len(to_compress)
