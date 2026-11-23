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


# ═══════════════════════════════════════════════════════════
# 装不下时给「目录 + 相关片段」，不是只截开头（2026-11-24）
#
# 判据来自 2026-11-23 那次「@ 了文档却答找不到」：只给开头时，模型会把
# 「我只看到前两千字」当成「后面没有」。
# ═══════════════════════════════════════════════════════════

_HEADED = (
    "导语一句\n\n"
    "## 一、起点\n\n" + "铺垫。" * 400 + "\n\n"
    "## 二、检索怎么做的\n\n" + "这里讲检索增强的具体做法。" * 60 + "\n\n"
    "## 三、结论\n\n" + "收尾。" * 400 + "\n"
)


def test_长文档带问题_给目录与相关片段():
    _clear()
    out, _ = build_doc_context(_FakeStore(_HEADED), ["d1"], 300_000,
                               query="检索怎么做的")

    assert "目录：" in out and "二、检索怎么做的" in out
    assert "与问题相关的片段" in out
    assert "检索增强的具体做法" in out
    assert f"共 {len(_HEADED)} 字" in out
    # 说明必须点出还有别的入口，否则模型不知道能读；offset 要给具体数
    assert "lookup_doc" in out
    assert "offset=" in out


def test_长文档没有问题时_只给目录():
    _clear()
    out, _ = build_doc_context(_FakeStore(_HEADED), ["d1"], 300_000)

    assert "目录：" in out
    assert "一、起点" in out
    assert "与问题相关的片段" not in out


def test_服务端检索那一轮_说明里不提工具():
    """⚠️ 那一轮一个本地工具都没注册。说了「可以用 lookup_doc」，
    模型会去调一个不存在的工具。"""
    _clear()
    out, _ = build_doc_context(_FakeStore(_HEADED), ["d1"], 300_000,
                               query="检索", can_read=False)

    assert "lookup_doc" not in out
    assert "本轮没有读取工具" in out


def test_按_part注入笔记而不是逐字稿():
    _clear()
    notes = "## 笔记一\n\n" + "笔记正文。" * 500
    out, _ = build_doc_context(
        _FakeStore("逐字稿正文。" * 500), ["d1"], 300_000,
        part="notes", reader=lambda _id, _p: notes)

    assert "笔记正文" in out
    assert "逐字稿正文" not in out
    assert "· notes" in out          # 标签写明注入的是哪一份


def test_读产物失败时退回逐字稿且不崩():
    _clear()

    def _boom(_id, _part):
        raise RuntimeError("产物读不出来")

    out, _ = build_doc_context(_FakeStore("逐字稿正文。"), ["d1"], 300_000,
                               part="notes", reader=_boom)

    assert "逐字稿正文" in out
    assert "· notes" not in out


def test_摘要形态不超出_limit():
    """⚠️ `limit` 是单篇注入的唯一闸门：开头 + 无上限目录 + 三段片段自然拼起来
    能到它的两倍以上（一篇几十节的笔记，目录项本身就上千字）。"""
    _clear()
    md = "\n\n".join(f"## 第{i}节\n\n" + "内容。" * 60 for i in range(80))
    out, _ = build_doc_context(_FakeStore(md), ["d1"], 300_000,
                               doc_mode="summary", query="第7节")
    assert len(out) < 1300, f"实际 {len(out)} 字，闸门是 1000 + 说明"


def test_产物正文不用逐字稿的缓存():
    """缓存失效判据是 transcript 的 contentHash，拿它判产物有没有变是错的 ——
    产物重新生成后 hash 不变，会把上一份的截断文本当成当前正文。"""
    _clear()
    texts = ["旧内容。" * 600, "新内容。" * 600]
    calls = {"n": 0}

    def _reader(_id, _part):
        t = texts[min(calls["n"], 1)]
        calls["n"] += 1
        return t

    out1, _ = build_doc_context(_FakeStore("逐字稿。"), ["d1"], 300_000,
                                part="notes", reader=_reader)
    out2, _ = build_doc_context(_FakeStore("逐字稿。"), ["d1"], 300_000,
                                part="notes", reader=_reader)
    assert "旧内容" in out1
    assert "新内容" in out2
