"""向量索引后台 worker —— 增量 / 失败闸门 / 对账。

运行：cd backend && pytest tests/test_vector_worker.py

⚠️ 本文件守的是四条**静默失效**（都不抛错，不这么写测试就看不出来）：
  1. 增量判据退化成「整篇重编码」—— 每次都成功、产物完全正确，只是**每次都花钱**；
  2. 增量判据退化成「内容变了却不更新」—— 检索照样返回，只是返回的是旧文本的语义；
  3. 失败计数只在内存 —— 重启归零 ⇒ 无限重试 ⇒ 无限烧钱；
  4. 对账把 `failed` 改回 `pending` —— `fail_count` 被清零，闸门永久失效。

⚠️ 第 1 条用的是「在**开头**插入一段」这个场景：位置匹配（旧块 i vs 新块 i）在这里
会把后面每一块都判成「变了」。只有内容匹配能过这条测试。
"""
import asyncio
import contextlib
import os
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.core.vector_store import VectorStore
from app.services.vector_worker import VectorWorker

SIZE = 200


def _p(tag: str, n: int = 150) -> str:
    """造一个长度 150 的段落。150 字两段就超过 size(200)，故每段各自成块 ——
    块边界由段落决定而不是由硬切决定，插入一段才不会移动后面所有块的内容。"""
    return tag + "x" * (n - len(tag))


class _FakeStore:
    def __init__(self, docs: dict[str, str]):
        self.docs = dict(docs)

    def get_transcript(self, tx_id):
        if tx_id not in self.docs:
            return None
        return {"id": tx_id, "title": tx_id, "rawText": self.docs[tx_id]}

    def get_markdowns_by_transcript(self, _tx_id, mode=""):
        return []

    def list_transcripts(self, filters=None):
        return [{"id": k} for k in self.docs]


class _FakeEngine:
    name = "fake-embedding"
    model_id = "fake-model"
    dimension = 4
    capabilities = ["embedding"]

    def __init__(self, boom=None, short_return=False):
        self.calls: list[list[str]] = []       # 每次 embed 的入参（一批一条记录）
        self._boom = boom                      # (text) -> bool
        self._short = short_return

    @property
    def encoded(self) -> list[str]:
        return [t for batch in self.calls for t in batch]

    async def embed(self, texts):
        self.calls.append(list(texts))
        for t in texts:
            if self._boom and self._boom(t):
                raise RuntimeError("编码失败")
        out = [[float(len(t)), 0.5, 0.25, 0.125] for t in texts]
        return out[:-1] if self._short else out


@contextlib.contextmanager
def _setup(docs, *, engine=None, chunk_size=SIZE, pending=True, **kw):
    d = tempfile.mkdtemp(prefix="limo_vec_worker_")
    try:
        vs = VectorStore(d)
        eng = engine or _FakeEngine()
        # 与 main.py 同款：口径先落库，worker 才有资格写
        assert vs.ensure_identity(eng.model_id, eng.dimension, chunk_size) is True
        store = _FakeStore(docs)
        # ⚠️ worker **只消费已被标记的文档** —— 标记是写点（摄入 / 生成完成 / 编辑保存）
        # 的职责。测试里显式模拟这一步，否则队列永远是空的（那不是 bug，是设计）。
        if pending:
            for tx_id in store.docs:
                vs.mark_pending(tx_id)
        yield VectorWorker(store, vs, eng, chunk_size=chunk_size, **kw), vs, eng, store
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── 首次索引 / 增量 ──

def test_first_index_encodes_every_chunk():
    docs = {"t1": _p("A") + "\n\n" + _p("B")}
    with _setup(docs) as (w, vs, eng, _store):
        stats = asyncio.run(w.run_once())
        assert stats == {"indexed": 1, "skipped": 0, "failed": 0, "stopped": None}
        assert len(eng.encoded) == 2                       # 两块都编码
        assert [c["chunkIdx"] for c in vs.get_chunks("t1")] == [0, 1]
        st = vs.get_state("t1")
        assert st["status"] == "ok"
        assert st["chunkCount"] == 2
        assert st["contentHash"]                            # 写入但仍不读（见模块文档）


def test_unchanged_content_calls_nothing():
    """写点重新打 pending（内容没变）→ 零编码调用。这就是「编辑没改到这块」的常态。"""
    docs = {"t1": _p("A") + "\n\n" + _p("B")}
    with _setup(docs) as (w, vs, eng, _store):
        asyncio.run(w.run_once())
        n = len(eng.calls)
        vs.mark_pending("t1")                               # 模拟一次写点
        stats = asyncio.run(w.run_once())
        assert stats["skipped"] == 1 and stats["indexed"] == 0
        assert len(eng.calls) == n                          # ⚠️ 一次调用都没新增


def test_only_changed_chunk_is_encoded():
    docs = {"t1": _p("A") + "\n\n" + _p("B")}
    with _setup(docs) as (w, vs, eng, store):
        asyncio.run(w.run_once())
        eng.calls.clear()

        store.docs["t1"] = _p("A") + "\n\n" + _p("B2")      # 只改第二块
        vs.mark_pending("t1")
        stats = asyncio.run(w.run_once())

        assert stats["indexed"] == 1
        assert len(eng.calls) == 1
        assert eng.encoded == [_p("B2")]                    # 只编码变了的那块
        assert len(vs.get_chunks("t1")) == 2


def test_insert_paragraph_does_not_reencode_everything():
    """⚠️ 本轮最关键的一条：**按内容匹配**而非按位置匹配。

    位置匹配的失效形态：在开头插入一段，`旧[0]` 已由 A 变成 C ⇒ i=0 变；
    `旧[1]=A` 与新 `[1]=A` 恰好相同，但 `旧[1]=B` 与新 `[2]=B` 错位 ⇒ 后面全判「变了」。
    内容匹配则只编码真正新增的 C，A / B 的向量原样复用 —— 而**两种实现都不报错**，
    差别只在账单上。
    """
    docs = {"t1": _p("A") + "\n\n" + _p("B")}
    with _setup(docs) as (w, vs, eng, store):
        asyncio.run(w.run_once())
        eng.calls.clear()

        store.docs["t1"] = _p("C") + "\n\n" + _p("A") + "\n\n" + _p("B")
        vs.mark_pending("t1")
        asyncio.run(w.run_once())

        assert eng.encoded == [_p("C")]                     # 只编码新增块
        assert [c["text"] for c in vs.get_chunks("t1")] == [_p("C"), _p("A"), _p("B")]


def test_shrink_removes_stale_chunks():
    docs = {"t1": _p("A") + "\n\n" + _p("B") + "\n\n" + _p("C")}
    with _setup(docs) as (w, vs, eng, store):
        asyncio.run(w.run_once())
        assert len(vs.get_chunks("t1")) == 3

        store.docs["t1"] = _p("A")                          # 删掉两段
        vs.mark_pending("t1")
        asyncio.run(w.run_once())
        assert len(vs.get_chunks("t1")) == 1                # 多余块必须消失（不是叠加）


def test_empty_doc_marks_ok_with_zero_chunks():
    """空文档：块数 0、**零编码调用**，但状态必须推进到 `ok`（否则它永远占着队列）。"""
    with _setup({"t1": ""}) as (w, vs, eng, _store):
        stats = asyncio.run(w.run_once())
        assert stats["skipped"] == 1
        assert eng.calls == []
        assert vs.get_state("t1")["status"] == "ok"
        assert vs.get_state("t1")["chunkCount"] == 0


# ── 失败闸门 ──

def test_consecutive_failures_stop_the_run():
    """连续 3 篇失败 → 本轮停。单篇偶发不该停，连续失败是 Key/网络问题，继续跑只是烧钱。"""
    docs = {f"t{i}": _p("A") for i in range(5)}
    eng = _FakeEngine(boom=lambda t: True)
    with _setup(docs, engine=eng) as (w, vs, _eng, _store):
        stats = asyncio.run(w.run_once())
        assert stats["failed"] == 3
        assert stats["stopped"]                             # 非 None = 被闸门中断
        assert len(eng.calls) == 3                          # 第 4、5 篇根本没试
        for tx in ("t0", "t1", "t2"):
            assert vs.get_state(tx)["failCount"] == 1
        # t3 / t4 仍是 pending（未被触碰）——闸门停的是**本轮**，不是把它们标失败
        assert vs.get_state("t3")["status"] == "pending"
        assert vs.get_state("t3")["failCount"] == 0


def test_failed_doc_is_not_retried_within_one_run():
    """⚠️ 失败但未到上限的篇目，下一批 `retry_ids` 还会吐出来 ——
    没有 `attempted` 守卫就会在同一轮里死循环（每圈都计费）。"""
    eng = _FakeEngine(boom=lambda t: True)
    with _setup({"t1": _p("A")}, engine=eng) as (w, vs, _eng, _store):
        stats = asyncio.run(w.run_once())
        assert stats["failed"] == 1
        assert len(eng.calls) == 1                          # 只试一次
        assert vs.get_state("t1")["failCount"] == 1


def test_fail_count_persists_and_gates_retry():
    """`fail_count` 落盘 + 到上限后不再被取（跨轮次的闸门）。"""
    eng = _FakeEngine(boom=lambda t: True)
    with _setup({"t1": _p("A")}, engine=eng, max_fail=2) as (w, vs, _eng, _store):
        asyncio.run(w.run_once())
        asyncio.run(w.run_once())                            # 第二轮允许重试一次
        assert vs.get_state("t1")["failCount"] == 2
        n = len(eng.calls)
        asyncio.run(w.run_once())                            # 到上限 → 不再取
        assert len(eng.calls) == n
        assert vs.get_state("t1")["status"] == "failed"


def test_embed_count_mismatch_is_an_error():
    """引擎违约（返回条数不符）必须显式失败 —— 错位会让「文本 A 的向量」挂到文本 B 上。"""
    eng = _FakeEngine(short_return=True)
    with _setup({"t1": _p("A") + "\n\n" + _p("B")}, engine=eng) as (w, vs, _eng, _store):
        stats = asyncio.run(w.run_once())
        assert stats["failed"] == 1
        assert vs.get_state("t1")["status"] == "failed"
        assert vs.get_chunks("t1") == []                     # 半批向量不落库


def test_unavailable_vec_stops_immediately():
    d = tempfile.mkdtemp(prefix="limo_vec_bad_")
    try:
        with open(os.path.join(d, "vec.db"), "wb") as f:
            f.write(b"not a sqlite file")
        vs = VectorStore(d)
        assert vs.available is False
        eng = _FakeEngine()
        w = VectorWorker(_FakeStore({"t1": _p("A")}), vs, eng, chunk_size=SIZE)
        stats = asyncio.run(w.run_once())
        assert stats["indexed"] == 0 and stats["stopped"]
        assert eng.calls == []                               # 库坏了就不该花钱
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_worker_requires_engine():
    with _setup({}, engine=_FakeEngine()) as (w, vs, eng, store):
        with pytest.raises(ValueError):
            VectorWorker(store, vs, None)


# ── 对账 ──

def test_reconcile_removes_orphans():
    """`vec_state` 有、文档表无 → 删块与状态（否则检索会返回一个点不开的文档）。"""
    with _setup({}, pending=False) as (w, vs, _eng, store):
        store.docs["t1"] = _p("A")
        vs.mark_pending("t1")
        asyncio.run(w.run_once())
        store.docs.pop("t1")                                 # 文档被删，向量侧漏删
        r = w.reconcile()
        assert r == {"orphans": 1, "unmarked": 0}
        assert vs.known_ids() == set()
        assert vs.get_chunks("t1") == []


def test_reconcile_marks_missing_docs_pending():
    """文档表有、`vec_state` 无 → 补 pending（历史库 / 写点异常）。"""
    with _setup({}) as (w, vs, _eng, store):
        store.docs["t9"] = _p("A")
        r = w.reconcile()
        assert r == {"orphans": 0, "unmarked": 1}
        assert vs.get_state("t9")["status"] == "pending"


def test_reconcile_does_not_reset_fail_count():
    """⚠️ §4.6 第 ③ 条**不在这里做**：把 `failed` 改回 `pending` 会归零 `fail_count`，
    闸门永久失效。重试由 `run_once` 的 `retry_ids` 承担，状态一个字节都不动。"""
    with _setup({"t1": _p("A")}, engine=_FakeEngine(boom=lambda t: True)) as (
            w, vs, _eng, _store):
        asyncio.run(w.run_once())
        assert vs.get_state("t1")["failCount"] == 1
        w.reconcile()
        st = vs.get_state("t1")
        assert st["status"] == "failed"
        assert st["failCount"] == 1


def test_reconcile_is_idempotent():
    docs = {"t1": _p("A")}
    with _setup(docs) as (w, vs, _eng, _store):
        asyncio.run(w.run_once())
        assert w.reconcile() == {"orphans": 0, "unmarked": 0}
        assert w.reconcile() == {"orphans": 0, "unmarked": 0}
        assert vs.get_state("t1")["status"] == "ok"


# ── 常驻循环 ──

def test_loop_exits_when_stop_is_set():
    """`stop` 已置位时，跑完一次对账就该直接返回（不 sleep、不空转）。"""
    with _setup({"t1": _p("A")}) as (w, vs, eng, _store):
        stop = asyncio.Event()
        stop.set()
        asyncio.run(asyncio.wait_for(w.loop(stop, interval=0.01), timeout=2))
        assert eng.calls == []                               # 对账补了 pending，但循环没进去
        assert vs.get_state("t1")["status"] == "pending"


def test_loop_indexes_then_waits():
    """循环里真的会推进队列（并且 `stop` 能中断睡眠）。"""
    with _setup({"t1": _p("A")}) as (w, vs, eng, _store):
        stop = asyncio.Event()

        async def _drive():
            task = asyncio.create_task(w.loop(stop, interval=5.0))
            for _ in range(50):                              # 最多等 0.5s
                if vs.get_state("t1") and vs.get_state("t1")["status"] == "ok":
                    break
                await asyncio.sleep(0.01)
            stop.set()
            await asyncio.wait_for(task, timeout=2)

        asyncio.run(_drive())
        assert vs.get_state("t1")["status"] == "ok"
        assert len(eng.calls) == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
