"""检索端点的 HTTP 层测试（`/api/search/status` / `/api/search/reindex`）。

**为什么需要这个文件**：这两个端点此前**没有任何 HTTP 层测试**。
`test_search.py` 测的是 `SearchEngine` 类本身 —— 「引擎能重建」与「端点调通了重建」
是两件事（模式 #51：能力存在 ≠ 该路径在用它）。而 `POST /api/search/reindex`
是设置页那个「重建索引」按钮的全部实现。

第四轮审查把它的同步 `engine.rebuild(...)` 改成 `await asyncio.to_thread(...)`
（P2-03）：改动只有一行，但**传参对不对、端点还通不通没有任何判据** ——
`rebuild` 的 `vacuum` 是 keyword-only，一旦签名或参数顺序漂移，用户看到的是
「点了重建没反应」，而所有单测照样全绿。

⚠️ 本文件**不进 `TestClient` 上下文**（不跑 startup event），手动注入临时
Store + SearchEngine —— 与 `test_delete_index.py` 同一手法，理由见该文件注释。

对照实验（均实测）：
  A. 把 `await asyncio.to_thread(engine.rebuild, transcripts, _get_content, vacuum=True)`
     写成位置形式 `engine.rebuild(transcripts, _get_content, True)`（`vacuum` 是
     keyword-only）→ `TypeError` → 500 → `test_reindex_via_http_rebuilds_index` 红
  B. 删掉 `search_reindex` 里的 `ensure_trusted_request(request)`
     → `test_reindex_rejects_untrusted_origin` 红
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from fastapi.testclient import TestClient

from app.core.search import SearchEngine
from app.core.types import RawMetadata, RawTranscript, SourceType
from app.storage.store import Store
from main import app

_NEEDLE = "needle"

#: 前端 `apiFetch` 每个请求都带它（`frontend/src/api/core.ts::CLIENT_HEADERS`）
_CLIENT = {"X-LiMo-Client": "1"}


def _setup() -> tuple[TestClient, str]:
    """临时 Store + SearchEngine + 两篇带同一关键词的文档。

    ⚠️ 三个 `app.state` 属性**必须显式赋值**：不赋的话会拿到先前某个跑了 startup
    的测试留下的实例（指向已删的临时目录）—— 表现为顺序依赖的随机红。
    """
    d = tempfile.mkdtemp(prefix="limo_search_api_")
    store = Store(d)
    app.state.store = store
    app.state.search_engine = SearchEngine(d)      # 建表，但不灌内容
    app.state.vector_store = None
    app.state.vector_runtime = None
    app.state.vector_search = None

    for i in (1, 2):
        store.save_transcript(RawTranscript(
            id=f"tr_{i}", title=f"标题{i}", source_type=SourceType.WEBPAGE,
            engine="trafilatura", raw_text=f"第 {i} 篇的正文里都有 {_NEEDLE} 这个词。",
            content_hash=f"h{i}", metadata=RawMetadata(),
        ))
    return TestClient(app), d


def test_reindex_via_http_rebuilds_index():
    """点「重建索引」→ 索引真的被灌满（篇数 + 可搜性**两条都断**）。

    ⚠️ 只断 `docCount` 不够：`docCount` 来自 `SELECT count(*)`，一个「表建对了
    但灌空」的实现也会让篇数对不上 —— 所以再搜一次。反过来只断搜索也不够：
    搜索能命中而篇数报 0 说明状态端点读错了地方。
    """
    c, d = _setup()
    try:
        # 前提断言：重建前是空的（否则「重建成功」可能只是读了旧内容）
        before = c.get("/api/search/status").json()
        assert before["docCount"] == 0, f"前提不成立：{before}"

        r = c.post("/api/search/reindex", headers=_CLIENT)
        assert r.status_code == 200, r.text
        st = r.json()
        assert st["available"] is True and st["docCount"] == 2, st
        assert st["lastError"] is None, st
        assert st["rebuiltAt"], "重建成功要记下时间（设置页显示『上次重建于』）"
        # 状态里必须带向量段（`_vector_status` 走的是 `app.state`，未初始化时给默认值）
        assert st["vector"]["enabled"] is False, st["vector"]

        hits = c.get("/api/search", params={"q": _NEEDLE}).json()["hits"]
        assert {h["id"] for h in hits} == {"tr_1", "tr_2"}, \
            f"重建后必须能搜到两篇，实际 {hits}"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_reindex_rejects_untrusted_origin():
    """陌生 Origin → 403（有副作用的重建不能由任意网页触发）。

    ⚠️ 与上一条**成对**：只测成功路径的话，把 `ensure_trusted_request` 删掉
    也不会有任何用例红。
    """
    c, d = _setup()
    try:
        r = c.post("/api/search/reindex", headers={"Origin": "https://evil.example"})
        assert r.status_code == 403, r.text
        assert app.state.search_engine.status()["docCount"] == 0, \
            "被拒的请求不该先把索引重建了"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_status_reports_vector_block_when_uninitialized():
    """向量库未初始化 → `vector` 段给出明确的 `enabled: false` + 原因。

    ⚠️ 这一段**不能省成 `null`**：前端 `SearchIndexCard` 据此渲染开关与说明，
    `null` 会让「后端没接向量」与「向量关着」看起来一样。
    """
    c, d = _setup()
    try:
        st = c.get("/api/search/status").json()
        assert st["available"] is True
        vec = st["vector"]
        assert vec["enabled"] is False and vec["reason"], vec
        assert vec["docs"] == {} and vec["chunks"] == 0 and vec["sizeBytes"] == 0, vec
    finally:
        shutil.rmtree(d, ignore_errors=True)
