"""只收纳的占位正文**不许**流进下游（2026-11-07，代码审查第 3 条）。

## 这里守的是什么

「导入与转录分开」之后，库里会长期存在一批 `transcribeState == "pending"` 的文档，
它们的 `rawText` 是一段占位注释（`<!-- limo-pending:tr_… -->` 加两句说明），
不是正文。占位文本长得像正常 Markdown，所以任何按「正文非空」判断的下游都会放行它：

| 下游 | 放行的后果 |
|---|---|
| 全文索引 | 「这篇文档已收纳」「点开始转录」变成可搜噪音，每条 pending 文档都命中 |
| 向量 worker | 给占位文本编一次码 —— **云端引擎下是每篇一次计费调用** |
| 生成端点 | 拿占位文本生成一篇结构完整的笔记，通篇在讲「这篇还没有转录」 |

## 判据只有一处

三处判据都走 `doc_text.transcript_pending`（`== "pending"` 而不是 `!= "done"`：
缺键是历史文档，读作 `done`；反过来写会把**全部历史文档**判成待转录，它们会突然
搜不到自己的正文，且没有任何报错）。

## 对照实验

    A. `search_index_text` 去掉 pending 早退 → 第 1 组红（占位文本进了索引）
    B. `update_index` 把 `not skip_vec` 去掉 → 第 2 组红（pending 也打了向量标记）
    C. `_ensure_transcribed` 改成永远 return → 第 3 组红（两个端点都放行）
    D. `transcript_pending` 写成 `tx.get("transcribeState") != "done"`（**不带**默认值）
       → 第 4 组红（缺键的历史文档被误判成待转录）。
       ⚠️ 写成 `.get(…, "done") != "done"` 时本条**照样绿** —— 两者对「缺键」的结论
       相同，差别只在有没有把「缺键 = done」这条事实写在取值处。真正危险的是漏掉
       默认值那一种，别拿这个等价写法证明判据有区分力。

⚠️ 不碰真实数据目录：临时 `Store` + 裸 FastAPI app，不进 TestClient 的 startup。
"""
import asyncio
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.core.errors import AppError
from app.core.search import SearchEngine, update_index
from app.core.types import RawTranscript, RawMetadata
from app.generation.doc_text import search_index_text, transcript_pending
from app.storage.store import Store

import app.routers.generate as G

_TMP: list[str] = []
_STORE = Store(tempfile.mkdtemp(prefix="limo_pending_guard_"))
_TMP.append(str(_STORE.root))

PLACEHOLDER = (
    "<!-- limo-pending:tr_abc123def456 -->\n"
    "> 这篇文档已收纳，正文还没有转录。\n"
    "> 在预览区点「开始转录」即可提取正文。"
)


def _save(tx_id: str, *, title: str, raw: str, state: str) -> str:
    ok = _STORE.save_transcript(RawTranscript(
        id=tx_id, title=title, source_type="video", engine="pending"
        if state == "pending" else "fake", raw_text=raw,
        content_hash=f"hash-{tx_id}", metadata=RawMetadata(),
        transcribe_state=state))
    assert ok, f"造文档失败：{tx_id}"
    return tx_id


class _FakeVec:
    """只记 `mark_pending` 调了几次 —— 这就是第 2 组要观测的东西。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def mark_pending(self, tx_id: str) -> None:
        self.calls.append(tx_id)


# ═══════════════════════════════════════════════════════════
# 1. 占位正文不进索引，标题照进（「记得标题就搜得到」）
# ═══════════════════════════════════════════════════════════

def test_占位正文不进索引文本_但真文档照旧():
    """对照实验 A：去掉 `search_index_text` 里的 pending 早退 → 本组红。"""
    pending = _save("tr_pend1", title="唯一标题甲", raw=PLACEHOLDER, state="pending")
    done = _save("tr_done1", title="唯一标题乙", raw="这是一篇真正的正文甲", state="done")

    assert search_index_text(_STORE, pending) == "", \
        "占位文本（含「已收纳」「开始转录」）不该成为索引内容"
    assert search_index_text(_STORE, done) == "这是一篇真正的正文甲", \
        "done 的文档必须逐字取 rawText（这里是零回归的对照组）"


def test_pending文档标题仍可搜_正文词搜不到():
    """返回空串而**不是**跳过整条索引：`SearchEngine.index` 照常写标题列。

    用户收纳完过几天回来找的正是标题 —— 「能搜到」与「占位文本别当正文」要同时成立。
    """
    eng = SearchEngine(str(_STORE.root))
    pending = _save("tr_pend2", title="标题丙检索词", raw=PLACEHOLDER, state="pending")
    update_index(eng, _STORE, pending, vec=None)

    assert pending in {h["id"] for h in eng.search("标题丙检索词")}, \
        "只在标题列命中也要搜得到 —— 这是接纳收的唯一用处"
    assert pending not in {h["id"] for h in eng.search("开始转录")}, \
        "占位文本里的词不该命中；命中了说明索引拿到了占位正文"


# ═══════════════════════════════════════════════════════════
# 2. 不给占位篇打向量 pending（那是每篇一次计费编码）
# ═══════════════════════════════════════════════════════════

def test_pending文档不打向量标记_done照打():
    """对照实验 B：`update_index` 去掉 `not skip_vec` → 本组第一条红。"""
    eng = SearchEngine(str(_STORE.root))
    pending = _save("tr_pend3", title="向量测试甲", raw=PLACEHOLDER, state="pending")
    done = _save("tr_done3", title="向量测试乙", raw="真正文乙", state="done")

    vec = _FakeVec()
    update_index(eng, _STORE, pending, vec=vec)
    assert vec.calls == [], (
        "占位篇打 pending = 让 worker 去编码一句「这篇文档已收纳」。云端 embedding "
        f"引擎下这是每篇一次计费调用。实际调用 {vec.calls}")

    update_index(eng, _STORE, done, vec=vec)
    assert vec.calls == [done], "对照组：done 的文档必须照旧打标记，否则语义索引永远缺这篇"


def test_读不到状态时_NOR_向量标记_字面索引照旧():
    """「读不到」按「不知道」处理：字面索引照旧跑，向量侧不标记。

    ⚠️ 宁可少标一次（下次转录/编辑会补上），也不要给一个可能没有正文的篇目
    排一次计费编码。
    """
    class _BrokenStore:
        def get_transcript(self, tx_id):
            raise RuntimeError("索引损坏")

    eng = SearchEngine(str(_STORE.root))
    vec = _FakeVec()
    update_index(eng, _BrokenStore(), "tr_any", vec=vec)
    assert vec.calls == [], "状态读不到时不许猜「大概有正文」，一律跳过"


# ═══════════════════════════════════════════════════════════
# 3. 生成端点拒绝占位正文（前端拦过 ≠ 接口层不用拦）
# ═══════════════════════════════════════════════════════════

class _Guard:
    semaphore = asyncio.Semaphore(1)


def _make_app() -> TestClient:
    app = FastAPI()
    app.include_router(G.router, prefix="/api")
    app.state.store = _STORE
    app.state.config = {"llm": {}, "scene_models": {}, "data_dir": str(_STORE.root)}
    app.state.guard = _Guard()

    @app.exception_handler(AppError)
    async def _handler(request, exc: AppError):
        return JSONResponse(status_code=200 if exc.recoverable else exc.status_code,
                            content=exc.to_dict())

    return TestClient(app)


def test_两个生成端点都拒绝pending文档():
    """对照实验 C：`_ensure_transcribed` 改成永远 return → 本组红。

    批量队列、外部脚本、将来的其它入口都直接打这两个端点 —— 前端禁用按钮
    挡不住它们。
    """
    client = _make_app()
    pending = _save("tr_pend4", title="待转录生成测试", raw=PLACEHOLDER, state="pending")

    r = client.post(f"/api/generate/{pending}", json={"mode": "notes"})
    assert r.json().get("code") == "GEN_NOT_TRANSCRIBED", r.text
    assert r.json().get("hint"), "必须给出下一步（去点「开始转录」），否则用户只知道不能生成"

    # 流式端点：raise 必须在 StreamingResponse 构造之前，所以拿到的仍是 JSON 错误体
    r2 = client.get(f"/api/generate/{pending}/stream")
    assert r2.json().get("code") == "GEN_NOT_TRANSCRIBED", r2.text


def test_守卫对done文档零回归_且文档不存在仍是NOT_FOUND():
    """既有判据不许被新守卫吞掉：先判「存不存在」，再判「转录没转录」。"""
    client = _make_app()
    r = client.post("/api/generate/tr_not_exist", json={"mode": "notes"})
    assert r.json().get("code") == "GEN_NOT_FOUND", r.text

    from app.routers.generate import _ensure_transcribed
    _ensure_transcribed({"transcribeState": "done"})      # 不抛
    _ensure_transcribed({})                                # 缺键 = done，不抛
    try:
        _ensure_transcribed({"transcribeState": "pending"})
    except AppError as e:
        assert e.code == "GEN_NOT_TRANSCRIBED"
    else:
        raise AssertionError("pending 必须抛错")


# ═══════════════════════════════════════════════════════════
# 4. 判据本身：缺键读作 done
# ═══════════════════════════════════════════════════════════

def test_缺键读作done_不读作pending():
    """对照实验 D：写成 `!= "done"` → 本条红。

    历史文档没有这个字段。反过来写，它们会突然搜不到自己的正文、也无法生成笔记，
    而库里一个字节都没变 —— 这种回归最难查。
    """
    assert transcript_pending({}) is False
    assert transcript_pending({"transcribeState": "done"}) is False
    assert transcript_pending({"transcribeState": "pending"}) is True
    assert transcript_pending(None) is False


def teardown_module():
    for d in _TMP:
        shutil.rmtree(d, ignore_errors=True)
    _TMP.clear()
