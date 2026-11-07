"""合并 = 纯转录合并（2026-11-07）。

## 这里守的是什么

「合并」与「综述」产出的都是 `sourceType="composite"` 的文档，区别只在**正文出自谁手**：
综述由模型按读者意图写，合并把各篇原文按顺序接起来。故合并这条链上有三个
不该出现的东西 —— 模型调用、预算判定、降级状态 —— 一旦混进来，产物看起来仍是
一份正常的合并文档，只是内容被改写过。

## 为什么单测直接调 `merge_sources` 而不是走 HTTP

HTTP 那一层只多两件事：`asyncio.to_thread` 与响应体拼装。第 2 组单独测它，
其余组直接调函数 —— 走 HTTP 会让每条断言都要先解一层 JSON。
**两边共用同一个 `Store` 实例的前提是本文件不并发。**

## 对照实验（四条均已真跑，改完后对应组确实变红，随后还原）

    A. `build_composite_raw_text(..., output_mode="merge")` 改成 `output_mode=""`
       → 第 3 组红（出处头不标来路，三个月后分不出这份是模型写的还是拼的）
    B. 调 `relocate_assets` 但丢弃返回值、正文仍用 `s.raw_text`
       → 第 4 组红（文件搬了、正文仍指旧名，与完全不搬运一模一样）
    C. `_load_sources(..., source_mode)` 换成固定的 `"raw"`
       → 第 7 组红（用户选了笔记却拿原文拼，产物上看不出异常）
    D. 在 `merge_sources` 函数体开头加一行 `plan_synthesis()`
       → 第 5 组红（综述那套窗口闸门被搬进来，超长材料会被判 reject）
"""
import os
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.core import transcript_meta as TM
from app.core.dependencies import get_store
from app.core.errors import AppError, SynthesisError
from app.core.types import (
    GenerateMode, MarkdownView, RawMetadata, RawTranscript, SourceType, Style,
)
from app.generation import synthesis as S
from app.storage.store import Store

import app.routers.synthesis as R

_TMP: list[str] = []


def _new_store() -> Store:
    d = tempfile.mkdtemp(prefix="limo_merge_")
    _TMP.append(d)
    return Store(d)


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    while _TMP:
        shutil.rmtree(_TMP.pop(), ignore_errors=True)


def _add_doc(store: Store, tx_id: str, title: str, raw: str,
             source_type: SourceType = SourceType.PDF) -> None:
    """写一篇源文档。

    ⚠️ 正文末尾的 `[源标识 {tx_id}]` 不是装饰：`save_transcript` 有**内容级
    SHA256 去重**，本文件多处让两篇正文完全相同（资产测试两篇都是同一张图），
    不加后缀第二篇会被静默拒绝 → 合并时报「源文档已不存在」，症状看起来像
    合并逻辑有 bug。
    """
    store.save_transcript(RawTranscript(
        id=tx_id, title=title, source_type=source_type, engine="pypdfium2",
        raw_text=f"{raw}\n\n[源标识 {tx_id}]", content_hash="",
        metadata=RawMetadata(), tags=[]))


def _add_notes(store: Store, md_id: str, tx_id: str, content: str) -> None:
    store.save_markdown(MarkdownView(
        id=md_id, transcript_id=tx_id, content=content,
        mode=GenerateMode.NOTES, style=Style.MINIMAL))


def _two_docs() -> Store:
    store = _new_store()
    _add_doc(store, "tr_a", "甲文", "甲的正文-AAA。")
    _add_doc(store, "tr_b", "乙文", "乙的正文-BBB。")
    return store


# ═══════════════════════════════════════════════════════════
# 1. 产物是什么
# ═══════════════════════════════════════════════════════════

def test_合并产物是普通文档且零模型调用():
    """合并落库后的形状必须与综述文档一致 —— 下游（库/搜索/导出/对话）靠它活着。"""
    store = _two_docs()
    result = S.merge_sources(store, ["tr_a", "tr_b"], title="两篇合并")

    assert result.mode == "merge"
    # 结构性自证：`merge_sources` 的签名里没有 llm / config 参数，这里断的是
    # 「结果如实上报 0 次调用」，防将来有人在里面塞一次「顺手整理」的调用。
    assert result.llm_call_count == 0

    saved = store.get_transcript(result.transcript_id)
    assert saved is not None
    assert saved["sourceType"] == "composite"
    assert saved["engine"] == "merge"
    assert saved["title"] == "两篇合并"
    # 落盘的就是返回的那一份：前端拿 `result.raw_text` 与随后重新拉取的内容
    # 必须逐字相同，否则「合并完成 → 点开预览」会看到另一份正文。
    assert saved["rawText"] == result.raw_text


def test_框架未给标题时用首篇标题加篇数():
    """用户不填标题是常态；默认名要能一眼看出「哪几篇、几篇」。"""
    store = _two_docs()
    result = S.merge_sources(store, ["tr_a", "tr_b"])
    assert result.title == "甲文 等 2 篇合并"
    assert store.get_transcript(result.transcript_id)["title"] == result.title


# ═══════════════════════════════════════════════════════════
# 2. 正文形态
# ═══════════════════════════════════════════════════════════

def test_正文按序拼接且每篇带序号标题():
    """序号不能省：两篇同名文档各写一个 `## 同一标题` 时，正文里没有任何东西能分开它们。"""
    store = _two_docs()
    raw = S.merge_sources(store, ["tr_a", "tr_b"], title="合并").raw_text

    assert "## 1. 甲文" in raw
    assert "## 2. 乙文" in raw
    assert raw.index("## 1. 甲文") < raw.index("## 2. 乙文"), "小节顺序必须跟随 doc_ids"
    # 两篇正文都在，且分处各自小节之下
    assert raw.index("甲的正文-AAA") > raw.index("## 1. 甲文")
    assert raw.index("甲的正文-AAA") < raw.index("## 2. 乙文")
    assert raw.index("乙的正文-BBB") > raw.index("## 2. 乙文")


def test_来源清单恒标未删节():
    """合并没有任何一篇被摘要或截断 —— 标「未删节」是事实，不是省略。"""
    store = _two_docs()
    raw = S.merge_sources(store, ["tr_a", "tr_b"], title="合并").raw_text
    assert raw.count("✓ 未删节") == 2


# ═══════════════════════════════════════════════════════════
# 3. 出处头标记来路（与「空串不写键」成对）
# ═══════════════════════════════════════════════════════════

def test_出处头标记_outputMode_merge():
    store = _two_docs()
    raw = S.merge_sources(store, ["tr_a", "tr_b"], title="合并").raw_text
    meta, corrupt = TM.parse_meta(raw)
    assert corrupt is False
    assert meta is not None
    assert meta["outputMode"] == "merge"
    assert meta["sourceDocIds"] == ["tr_a", "tr_b"]
    assert meta["sourceTitles"] == ["甲文", "乙文"]


def test_综述形态的出处头不写_outputMode_键():
    """零回归对照：空串时整个键不出现，既有综述文档的出处头逐字节不变。

    ⚠️ 这一条与上一条**成对**才有意义：只断「合并没有/综述也没有」测不出区分力，
    只断「合并有」则看不出旧数据是否被改动。
    """
    head_with = TM.build_composite_raw_text(
        ["tr_a"], ["h1"], [{"title": "甲文", "status": "full"}], "正文",
        output_mode="merge")
    head_without = TM.build_composite_raw_text(
        ["tr_a"], ["h1"], [{"title": "甲文", "status": "full"}], "正文")

    assert TM.parse_meta(head_with)[0]["outputMode"] == "merge"
    assert "outputMode" not in TM.parse_meta(head_without)[0]
    # 缺键读作综述，与 `transcribeState` 缺键读作 "done" 同一形态
    assert "outputMode" not in head_without.splitlines()[0]


# ═══════════════════════════════════════════════════════════
# 4. 资产搬运：复制 + 改写必须同时成立
# ═══════════════════════════════════════════════════════════

def test_资产复制到新文档并加源序号前缀():
    """⚠️ 本功能最容易白做的地方就在这：文件搬了、正文仍指旧名 ≡ 没搬。

    两篇都引用 `assets/img_0001.png`（不同内容），前缀因此不能省 ——
    少了 `d1_` / `d2_` 就是后一个覆盖前一个，两张图变成同一张。
    """
    store = _new_store()
    _add_doc(store, "tr_a", "甲文", "甲的内容 ![图](assets/img_0001.png)")
    _add_doc(store, "tr_b", "乙文", "乙的内容 ![图](assets/img_0001.png)")
    for tx_id, payload in (("tr_a", b"AAA-IMAGE"), ("tr_b", b"BBB-IMAGE")):
        assets = store.get_doc_dir(tx_id) / "assets"
        assets.mkdir(exist_ok=True)
        (assets / "img_0001.png").write_bytes(payload)

    result = S.merge_sources(store, ["tr_a", "tr_b"], title="带图的合并")
    raw = result.raw_text

    # ① 正文引用被改写
    assert "assets/d1_img_0001.png" in raw
    assert "assets/d2_img_0001.png" in raw
    assert "](assets/img_0001.png)" not in raw, "旧引用残留 = 合并产物里是裂图"

    # ② 文件真的复制到了新文档目录，且内容各对各的
    new_dir = store.get_doc_dir(result.transcript_id)
    assert (new_dir / "assets" / "d1_img_0001.png").read_bytes() == b"AAA-IMAGE"
    assert (new_dir / "assets" / "d2_img_0001.png").read_bytes() == b"BBB-IMAGE"


def test_资产越界引用原样保留不阻断合并():
    """`assets/../` 来自用户导入内容，路径守卫必须原样保留该引用而不是复制出去。"""
    store = _new_store()
    _add_doc(store, "tr_a", "甲文", "甲 ![x](assets/../../secret.txt)")
    _add_doc(store, "tr_b", "乙文", "乙的正文")
    result = S.merge_sources(store, ["tr_a", "tr_b"], title="越界合并")

    assert "](assets/../../secret.txt)" in result.raw_text
    new_dir = store.get_doc_dir(result.transcript_id)
    assert not (new_dir / "assets" / "secret.txt").exists()


# ═══════════════════════════════════════════════════════════
# 5. 不参与预算判定
# ═══════════════════════════════════════════════════════════

def test_合并不跑预算判定(monkeypatch):
    """拼接不发给模型，窗口大小与它无关。

    ⚠️ 对照实验：在 `merge_sources` 里加一次 `plan_synthesis(...)` 调用 → 本组红。
    这条不是防「算错」，是防**把综述的闸门搬进来** —— 搬进来之后超长材料会
    被判 `reject`，而用户要的只是把三篇拼在一起。
    """
    def _boom(*a, **kw):
        raise AssertionError("合并不得调用 plan_synthesis（窗口与拼接无关）")

    monkeypatch.setattr(S, "plan_synthesis", _boom)

    store = _new_store()
    big = "字" * 30_000
    for i in range(3):
        _add_doc(store, f"tr_{i}", f"长文{i}", big)
    result = S.merge_sources(store, ["tr_0", "tr_1", "tr_2"], title="长文合并")

    assert result.llm_call_count == 0
    assert len(result.raw_text) > 90_000
    assert store.get_transcript(result.transcript_id) is not None


# ═══════════════════════════════════════════════════════════
# 6. 篇数门槛
# ═══════════════════════════════════════════════════════════

def test_不足两篇显式报错():
    store = _two_docs()
    with pytest.raises(SynthesisError) as ei:
        S.merge_sources(store, ["tr_a"])
    assert ei.value.code == "SYNTH_TOO_FEW_SOURCES"
    # 业务状态该走 200 + 结构化错误，不是 500（项目模式 #36）
    assert ei.value.recoverable is True


def test_源文档已删显式报错():
    store = _two_docs()
    with pytest.raises(SynthesisError) as ei:
        S.merge_sources(store, ["tr_a", "tr_gone"])
    assert ei.value.code == "SYNTH_SOURCE_MISSING"


# ═══════════════════════════════════════════════════════════
# 7. 原料模式：notes 排除无笔记篇
# ═══════════════════════════════════════════════════════════

def test_笔记模式排除无笔记篇并如实上报():
    """两篇有笔记 + 一篇没有 → 合并成两篇的笔记拼接，被排除的篇 id 带出去。

    ⚠️ 对照实验：`source_mode` 固定成 `"raw"` → 正文里出现「原文独有句」、本组红。
    """
    store = _new_store()
    _add_doc(store, "tr_a", "甲文", "原文独有句-甲")
    _add_doc(store, "tr_b", "乙文", "原文独有句-乙")
    _add_doc(store, "tr_c", "丙文", "原文独有句-丙")
    _add_notes(store, "md_a", "tr_a", "笔记独有句-甲")
    _add_notes(store, "md_b", "tr_b", "笔记独有句-乙")

    result = S.merge_sources(store, ["tr_a", "tr_b", "tr_c"], title="笔记合并",
                             source_mode="notes")

    assert result.mode == "merge"
    assert "笔记独有句-甲" in result.raw_text
    assert "笔记独有句-乙" in result.raw_text
    assert "原文独有句-甲" not in result.raw_text, "两种原料模式互斥，不得混入原文"
    assert "丙文" not in result.raw_text, "无笔记的篇不得出现在来源清单里"

    # ⚠️ 是 **id** 不是标题：前端要拿它从选中集里做差集
    assert result.excluded == ["tr_c"]
    meta, _ = TM.parse_meta(result.raw_text)
    assert meta["sourceMode"] == "notes"
    assert meta["sourceDocIds"] == ["tr_a", "tr_b"]


def test_有笔记的篇不足两篇时显式报错():
    store = _new_store()
    _add_doc(store, "tr_a", "甲文", "正文甲")
    _add_doc(store, "tr_b", "乙文", "正文乙")
    _add_notes(store, "md_a", "tr_a", "笔记甲")

    with pytest.raises(SynthesisError) as ei:
        S.merge_sources(store, ["tr_a", "tr_b"], source_mode="notes")
    assert ei.value.code == "SYNTH_NOT_ENOUGH_NOTES"


# ═══════════════════════════════════════════════════════════
# 8. HTTP 端点：业务失败走 200 + code
# ═══════════════════════════════════════════════════════════

def test_合并端点走非流式且业务失败为_200_加_code():
    store = _two_docs()
    app = FastAPI()
    app.include_router(R.router, prefix="/api")
    app.dependency_overrides[get_store] = lambda: store

    @app.exception_handler(AppError)
    async def _handler(request, exc: AppError):
        return JSONResponse(status_code=200 if exc.recoverable else exc.status_code,
                            content=exc.to_dict())

    client = TestClient(app)

    ok = client.post("/api/synthesis/merge",
                     json={"doc_ids": ["tr_a", "tr_b"], "title": "两篇合并",
                           "source_mode": "raw"})
    assert ok.status_code == 200
    body = ok.json()
    assert body["title"] == "两篇合并"
    assert body["mode"] == "merge"
    assert body["excluded"] == []
    assert store.get_transcript(body["id"]) is not None

    bad = client.post("/api/synthesis/merge",
                      json={"doc_ids": ["tr_a"], "title": "", "source_mode": "raw"})
    # ⚠️ 前端只看 `response.ok` 会把这条当成功，然后拿着没有 id 的对象去渲染
    assert bad.status_code == 200
    assert bad.json()["code"] == "SYNTH_TOO_FEW_SOURCES"
