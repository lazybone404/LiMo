"""对话的文档注入：截断必须**说出来**（2026-11-24，借鉴 pi 的 `truncate.ts`）。

要防的失效是「模型把节选当成全文」：用户 @ 了一份几万字的讲稿问「原文里某句话」，
注入只带了开头两千字，模型据此答「原文没提到」。它没撒谎，只是不知道自己在看节选。
闸门装在注入层里，不指望模型自觉。
"""

from __future__ import annotations

import app.core.chat_context as cc
from app.core.chat_context import build_doc_context, DOC_FULL_LIMIT


class _FakeStore:
    """只实现 `build_doc_context` 用到的那一个方法。"""

    def __init__(self, raw_text: str, title: str = "测试文档", h: str = "h1"):
        self.raw_text = raw_text
        self.title = title
        self.h = h

    def get_transcript(self, did: str) -> dict:
        return {"title": self.title, "rawText": self.raw_text,
                "contentHash": self.h, "createdAt": "2026-11-24"}


def _ctx(raw: str, budget: int = 300_000) -> str:
    """预算给足 → 走 full 档（DOC_FULL_LIMIT）。"""
    out, _ = build_doc_context(_FakeStore(raw), ["d1"], budget)
    return out


def _clear():
    cc._doc_ctx_cache.clear()


def test_超长文档注入时带上字数说明():
    _clear()
    raw = "这是一句话。" * 500                      # 3000 字
    out = _ctx(raw)

    assert "其余部分没有进入上下文" in out
    assert f"共 {len(raw)} 字" in out
    assert f"只注入了开头 {DOC_FULL_LIMIT - 2} 字" in out   # 3 个点是 "..."，扣掉


def test_没超过上限的文档不加说明():
    # 对照：短文档不该出现这句话，否则模型会以为自己看到的是节选
    _clear()
    out = _ctx("很短的一段话。")
    assert "其余部分没有进入上下文" not in out


def test_缓存命中时说明仍在():
    # 说明接在缓存的纯截断文本之后，不进缓存 —— 第二次调用命中缓存也必须带上它，
    # 否则「第一次说清楚了、第二次又静默了」。
    _clear()
    raw = "这是一句话。" * 500
    _ctx(raw)
    out2 = _ctx(raw)
    assert "其余部分没有进入上下文" in out2


def test_内容变了之后字数跟着变():
    # 说明里的数字取自当前正文，不能是缓存里那份的旧值
    _clear()
    assert "共 3000 字" in _ctx("这是一句话。" * 500)
    assert "共 6000 字" in _ctx("这是一句话。" * 1000)
