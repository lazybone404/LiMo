"""P0-04 — 删除文档必须同步清理全文索引，且**失败不能静默**。

**为什么单开一个文件**：`test_search.py::test_delete_removes_hit` 已经测过「删除后不再命中」，
但它调的是 `SearchEngine.delete` **本身** —— 那条用例全绿，与路由层有没有调用它、
调用**失败**了怎么办，完全是两回事（模式 #51「能力存在 ≠ 该路径在用」）。
本文件补的正是这一段：**走 HTTP 删除端点**，覆盖调用成功 / engine 缺失 / 调用抛错三态。

**修复前的形状**（`routers/ingest.py`）：

    try:
        _engine = getattr(request.app.state, 'search_engine', None)
        if _engine: _engine.delete(tx_id)
    except Exception: pass          # ← 连 engine 为 None 也不留痕

store 里文档已经删了、索引里却还留着 → 搜索仍能命中，点开 404（「幽灵命中」）。
最难堪的是**日志里一个字都没有**：用户说「删了还搜得到」，我们无从判断是哪一步断的。

⚠️ 本文件**不进 TestClient 上下文**（不跑 startup event），手动注入临时 Store +
SearchEngine —— 与 `test_folders_api.py` 同一手法，理由见该文件注释。

对照实验（均已实测，证明本文件有区分力）：
  A. 把删除分支改回 `except Exception: pass` 且不记 warning
     → `test_warns_*` 两条红
  B. 删掉 `engine.delete(tx_id)` 调用 → `test_delete_via_http_removes_hit` 红
"""
import os
import shutil
import sys
import tempfile
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from fastapi.testclient import TestClient

import app.routers.ingest as ingest_mod
from app.core.search import SearchEngine
from app.core.types import RawMetadata, RawTranscript, SourceType
from app.storage.store import Store
from main import app

_NEEDLE = "needle"
_BODY = f"正文里藏着 {_NEEDLE} 这个关键词，前后都是别的内容。"


class _FakeLog:
    """只替换 `ingest` 模块的 `log` 名字（不动全局 `app.core.logging`）。

    直接 monkeypatch `app.core.logging.warning` 也会被捕获到，但那会同时改写
    其余所有模块的日志出口；换模块内的名字影响面恰好等于被测范围。
    """

    def __init__(self):
        self.warnings: list[tuple[str, dict]] = []

    def warning(self, msg, **kw):
        self.warnings.append((msg, kw))

    def info(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass

    def debug(self, *a, **k):
        pass


@contextmanager
def _capture_ingest_log():
    """捕获 `routers/ingest.py` 发出的 warning（退出时无条件还原）。"""
    fake = _FakeLog()
    original = ingest_mod.log
    ingest_mod.log = fake
    try:
        yield fake
    finally:
        ingest_mod.log = original


def _setup(*, with_engine: bool = True) -> tuple[TestClient, str]:
    """临时 Store + （可选）SearchEngine，返回 (client, 临时目录)。"""
    d = tempfile.mkdtemp(prefix="limo_del_index_")
    store = Store(d)
    app.state.store = store
    # ⚠️ `search_engine` 必须显式赋值：不赋的话会拿到先前某个跑了 startup 的
    # 测试留下的、指向已删临时目录的实例（顺序依赖，见 `test_folders_api.py`）。
    engine = SearchEngine(d) if with_engine else None
    app.state.search_engine = engine

    store.save_transcript(RawTranscript(
        id="tr_1", title="标题", source_type=SourceType.WEBPAGE,
        engine="trafilatura", raw_text=_BODY, content_hash="h1",
        metadata=RawMetadata(),
    ))
    if engine:
        engine.index("tr_1", "标题", _BODY)
    return TestClient(app), d


def test_delete_via_http_removes_hit():
    """正常路径：删文档 → `/api/search` 不再命中（并确认正常路径零 warning）。

    ⚠️ 前半段（先搜得到）是**前提断言**：没有它，一个「索引压根没建起来」的环境
    会让后半段恒真 —— 那就是模式 #38 说的「判据没有区分力」。
    """
    c, d = _setup()
    try:
        hits = c.get("/api/search", params={"q": _NEEDLE}).json()
        assert len(hits) == 1 and hits[0]["id"] == "tr_1", f"前提不成立：{hits}"

        with _capture_ingest_log() as fake:
            r = c.delete("/api/transcripts/tr_1")
        assert r.status_code == 200 and r.json()["status"] == "deleted", r.text

        assert c.get("/api/search", params={"q": _NEEDLE}).json() == [], \
            "删文档后仍能搜到 → 幽灵命中（路由层漏调 search_engine.delete）"
        assert fake.warnings == [], f"正常路径不该有 warning：{fake.warnings}"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_delete_warns_when_engine_missing():
    """索引未就绪（`search_engine` 为 None）→ 删除仍成功，但必须留痕。

    语义：文档确实删掉了（返回 200 是对的），代价是索引里的残留清不掉 ——
    这件事必须能从日志里看出来，否则「删了还搜得到」永远无法归因。

    对照实验：把 warning 那段删掉 → 本用例红（`warnings` 为空）。
    """
    c, d = _setup(with_engine=False)
    try:
        with _capture_ingest_log() as fake:
            r = c.delete("/api/transcripts/tr_1")
        assert r.status_code == 200, r.text
        assert app.state.store.get_transcript("tr_1") is None, "文档本身应已删除"

        assert len(fake.warnings) == 1, \
            f"索引不可用必须留 warning（这正是 `except Exception: pass` 吞掉的那格）：{fake.warnings}"
        assert fake.warnings[0][1].get("tx_id") == "tr_1", fake.warnings
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_delete_warns_when_index_fails():
    """`engine.delete` 抛错 → 删除仍成功（文档已消失是既成事实），但必须留痕。

    ⚠️ 这一格是原缺陷的**核心**：旧写法 `except Exception: pass` 在这里既吞掉了
    「索引没清掉」这个事实，也吞掉了异常本身 —— 删除接口返回 200，日志空白，
    用户唯一能观察到的现象是「搜索里那条还在」。
    """
    c, d = _setup()
    try:
        def _boom(tx_id):
            raise RuntimeError("db is locked")

        # 实例属性注入：monkeypatch 不参与，用 try/finally 手动还原
        engine = app.state.search_engine
        original = engine.delete
        engine.delete = _boom
        try:
            with _capture_ingest_log() as fake:
                r = c.delete("/api/transcripts/tr_1")
        finally:
            engine.delete = original

        assert r.status_code == 200, r.text
        assert app.state.store.get_transcript("tr_1") is None, "文档本身应已删除"

        assert len(fake.warnings) == 1, f"索引删除失败必须留 warning：{fake.warnings}"
        assert "db is locked" in str(fake.warnings[0][1].get("err", "")), \
            f"异常原因必须带上（否则排查不了是哪一步断的）：{fake.warnings}"
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS: {fn.__name__}")
            passed += 1
        except Exception:
            print(f"FAIL: {fn.__name__}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} tests passed")
    sys.exit(0 if passed == len(tests) else 1)
