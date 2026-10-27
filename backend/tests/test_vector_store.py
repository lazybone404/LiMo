"""向量索引存储层（`vec.db` 三表）—— 身份校验 / 状态机 / 块读写 / 写点接线。

运行：cd backend && pytest tests/test_vector_store.py

⚠️ 本文件守的是三条**静默失效**（都不抛错、单测若不这么写就看不出来）：
  1. 四元组不符却继续用 —— 余弦照样算得出一个数、检索照常返回、排序毫无意义；
  2. `mark_pending` 留旧 `content_hash` —— worker 走「hash 相等跳过整篇」，内容已变却不更新；
  3. `fail_count` 只在内存 —— 重启归零 ⇒ 无限重试 ⇒ 无限烧钱。
"""
import os
import shutil
import sys
import tempfile

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.core.search import SearchEngine, update_index
from app.core.vector_store import (SCHEMA_VERSION, VectorStore, chunk_hash,
                                   pack_vec, unpack_vec)


def _store():
    d = tempfile.mkdtemp(prefix="limo_vec_test_")
    return VectorStore(d), d


# ── 建表 / 身份 ──

def test_empty_db_status():
    """空库：可用、未初始化（identity=None）、各计数为 0。"""
    vs, d = _store()
    try:
        st = vs.status()
        assert st["available"] is True, st
        assert st["identity"] is None            # 从未建过 ≠ 口径不符
        assert st["docs"] == {"total": 0, "ok": 0, "pending": 0, "failed": 0}
        assert st["chunks"] == 0
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_ensure_identity_three_states():
    """首次写入 / 一致 / 任一不符 —— 三态必须分开。

    ⚠️ 若把「不符」也返回真，整条「违规向量不得参与计算」的防线就没了，
    而表现只是**排序变差**，看起来像「模型不行」。
    """
    vs, d = _store()
    try:
        # 首次：写入并可用
        assert vs.ensure_identity("dashscope:text-embedding-v4", 1024, 200) is True
        assert vs.read_identity()["dimension"] == 1024

        # 完全一致：可用，且不重写 built_at
        before = vs.read_identity()["builtAt"]
        assert vs.ensure_identity("dashscope:text-embedding-v4", 1024, 200) is True
        assert vs.read_identity()["builtAt"] == before

        # 四元组任一不符 → 不可用
        assert vs.ensure_identity("other-model", 1024, 200) is False
        assert vs.ensure_identity("dashscope:text-embedding-v4", 768, 200) is False
        assert vs.ensure_identity("dashscope:text-embedding-v4", 1024, 400) is False
        # ⚠️ 不符时**不得**改库：旧身份仍在，等用户确认后才重建
        assert vs.read_identity()["modelId"] == "dashscope:text-embedding-v4"
        assert vs.read_identity()["chunkSize"] == 200
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_schema_version_is_recorded():
    """`schema_version` 必须落库 —— 它是「切块规则改了」的唯一信号。"""
    vs, d = _store()
    try:
        vs.ensure_identity("m", 8, 200)
        assert vs.read_identity()["schemaVersion"] == SCHEMA_VERSION
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_reset_identity_clears_everything():
    """重建 = 清空块与状态 + 写新四元组（花钱且不可逆，只由用户确认触发）。"""
    vs, d = _store()
    try:
        vs.ensure_identity("m1", 4, 200)
        vs.mark_pending("tr_1")
        vs.replace_chunks("tr_1", [{"idx": 0, "text": "abc", "vec": np.ones(4)}])
        vs.reset_identity("m2", 8, 400)
        assert vs.read_identity()["modelId"] == "m2"
        assert vs.read_identity()["dimension"] == 8
        assert vs.status()["chunks"] == 0
        assert vs.status()["docs"]["total"] == 0
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── 状态机 ──

def test_mark_pending_clears_stale_hash_and_fail_count():
    """⚠️ `mark_pending` 必须把 `content_hash` 置空、`fail_count` 归零。

    - 留旧 hash：worker 的快速路径「hash 相等就跳过整篇」会错误命中，
      内容已变而索引未更新，**零报错**；
    - 留旧 fail_count：一篇曾经失败过的文档，修好后再也拿不到重试机会
      （达到上限被闸门挡住），而它看起来只是「没被索引」。
    """
    vs, d = _store()
    try:
        vs.mark_ok("tr_1", chunk_count=3, content_hash="h1")
        vs.mark_failed("tr_1", "超时")
        vs.mark_failed("tr_1", "超时")
        assert vs.get_state("tr_1")["failCount"] == 2

        vs.mark_pending("tr_1")
        st = vs.get_state("tr_1")
        assert st["status"] == "pending"
        assert st["contentHash"] is None, "pending 时必须清空 content_hash（否则快速路径误命中）"
        assert st["failCount"] == 0
        assert st["error"] is None
        # ⚠️ 已有块**不删** —— 块级 hash 增量要靠旧块比对（§4.4）
        assert vs.get_state("tr_1")["chunkCount"] == 3
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_mark_pending_is_idempotent():
    vs, d = _store()
    try:
        vs.mark_pending("tr_1")
        vs.mark_pending("tr_1")
        assert vs.status()["docs"]["total"] == 1
        assert vs.status()["docs"]["pending"] == 1
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_mark_failed_accumulates():
    """`fail_count` 是**花钱的闸门**：必须落盘、每次真 +1、到上限真的不再返回。"""
    vs, d = _store()
    try:
        for _ in range(3):
            vs.mark_failed("tr_1", "boom")
        assert vs.get_state("tr_1")["failCount"] == 3
        assert vs.get_state("tr_1")["status"] == "failed"
        # 到上限 3 → retry_ids 不再吐它
        assert vs.retry_ids(max_fail=3) == []
        assert vs.retry_ids(max_fail=4) == ["tr_1"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_requeue_failed_清空计数与哈希但保留块():
    """「重试失败项」：失败项全部回 pending，**其余状态一条都不动**。

    三条都要断，且每条修错的失效形态都不同：
    - `fail_count` 不清零 → 仍 ≥ 上限，下一轮 `_next_ids` 照样不取它，按钮点了没用；
    - `content_hash` 不清空 → 「先成功后失败」的篇会被整篇快速路径判「hash 相等」
      直接 `mark_ok`，**一次编码都不发却显示成功**；
    - `vec_chunks` 被删 → 块级增量失效，重试等于整库重花一遍钱。
    """
    vs, d = _store()
    try:
        # 先成功后失败：hash 留着上一次成功的值（这正是要清掉的那个）
        vs.mark_ok("tr_sel", chunk_count=1, content_hash="deadbeef")
        vs.replace_chunks("tr_sel", [{
            "idx": 0, "text": "正文", "hash": chunk_hash("正文"),
            "vec": pack_vec([0.1, 0.2]),
        }])
        for _ in range(5):
            vs.mark_failed("tr_sel", "boom")
        for _ in range(3):
            vs.mark_failed("tr_dead", "boom")
        vs.mark_ok("tr_ok", chunk_count=1, content_hash="keepme")
        vs.mark_pending("tr_pending")

        assert vs.requeue_failed() == 2
        sel = vs.get_state("tr_sel")
        assert (sel["status"], sel["failCount"], sel["error"]) == ("pending", 0, None)
        assert sel["contentHash"] is None
        assert sel["chunkCount"] == 1          # 块数不属于「状态」，不该被抹
        st = vs.get_state("tr_dead")
        assert (st["status"], st["failCount"], st["error"]) == ("pending", 0, None)
        # 块还在（块级增量照旧生效）
        assert [c["textHash"] for c in vs.get_chunks("tr_sel")] == [chunk_hash("正文")]
        # 已成功 / 已在队列的篇不受影响
        assert vs.get_state("tr_ok")["status"] == "ok"
        assert vs.get_state("tr_ok")["contentHash"] == "keepme"
        assert vs.get_state("tr_pending")["status"] == "pending"
        # 再点一次：没有失败项了，返回 0（幂等）
        assert vs.requeue_failed() == 0
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_take_pending_does_not_consume():
    """`take_pending` **只读不写**：worker 崩在中途时这条必须还在队列里。

    若在这里就改成别的状态，崩溃即丢任务，而表现只是「有几篇没被索引」。
    """
    vs, d = _store()
    try:
        vs.mark_pending("tr_a")
        vs.mark_pending("tr_b")
        assert vs.take_pending(limit=10) == ["tr_a", "tr_b"]
        assert vs.take_pending(limit=10) == ["tr_a", "tr_b"]   # 仍在
        assert vs.take_pending(limit=1) == ["tr_a"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── 块读写 ──

def test_replace_chunks_roundtrip():
    """写入 → 读回：索引 / 文本 / hash / 向量逐项一致，`vec` 能解回原值。"""
    vs, d = _store()
    try:
        v0 = np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32)
        v1 = np.array([0.5, 0.6, 0.7, 0.8], dtype=np.float32)
        vs.replace_chunks("tr_1", [
            {"idx": 0, "text": "第一块", "vec": v0},
            {"idx": 1, "text": "第二块", "hash": "custom", "vec": pack_vec(v1)},
        ])
        got = vs.get_chunks("tr_1")
        assert [c["chunkIdx"] for c in got] == [0, 1]
        assert got[0]["text"] == "第一块"
        assert got[0]["textHash"] == chunk_hash("第一块")     # 未传 hash 时自动算
        assert got[1]["textHash"] == "custom"                # 传了就用传的
        np.testing.assert_allclose(unpack_vec(got[0]["vec"]), v0)
        np.testing.assert_allclose(unpack_vec(got[1]["vec"]), v1)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_replace_chunks_is_replace_not_append():
    """第二次写入块数变少 → 多余块必须消失（不是叠加）。

    ⚠️ 叠加的失效形态：编辑把 10 块删成 3 块，旧 7 块的向量还在被检索到，
    用户点进去看不到那段内容 —— 且**没有任何报错**。
    """
    vs, d = _store()
    try:
        vs.replace_chunks("tr_1", [
            {"idx": i, "text": f"块{i}", "vec": np.ones(4)} for i in range(10)
        ])
        vs.replace_chunks("tr_1", [
            {"idx": i, "text": f"新块{i}", "vec": np.ones(4)} for i in range(3)
        ])
        got = vs.get_chunks("tr_1")
        assert len(got) == 3
        assert got[0]["text"] == "新块0"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_delete_doc_removes_chunks_and_state():
    vs, d = _store()
    try:
        vs.mark_ok("tr_1", chunk_count=1)
        vs.replace_chunks("tr_1", [{"idx": 0, "text": "x", "vec": np.ones(4)}])
        vs.delete_doc("tr_1")
        assert vs.get_chunks("tr_1") == []
        assert vs.get_state("tr_1") is None
        assert vs.known_ids() == set()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_known_ids_lists_state_rows():
    vs, d = _store()
    try:
        vs.mark_pending("tr_a")
        vs.mark_ok("tr_b", chunk_count=0)
        assert vs.known_ids() == {"tr_a", "tr_b"}
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── 坏库不炸启动 ──

def test_corrupt_db_does_not_raise():
    """坏的 `vec.db` 只让语义检索不可用，**不能让应用起不来**。

    与 `SearchEngine` 同款取舍：所有文档仍能从磁盘读到、字面检索照常。
    """
    d = tempfile.mkdtemp(prefix="limo_vec_bad_")
    try:
        with open(os.path.join(d, "vec.db"), "wb") as f:
            f.write(b"not a sqlite file")
        vs = VectorStore(d)                    # ← 不许抛
        assert vs.available is False
        assert vs.last_error
        # 不可用时所有方法都要安全空转，不能抛
        assert vs.mark_pending("tr_1") is None
        assert vs.take_pending() == []
        assert vs.get_chunks("tr_1") == []
        assert vs.read_identity() is None
        assert vs.status()["available"] is False
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── 写点接线 ──

class _FakeStore:
    """够 `search_index_text` 用的最小 store。"""

    def __init__(self, raw: str = "原文", notes: str | None = None):
        self._raw, self._notes = raw, notes

    def get_transcript(self, _tid):
        return {"id": "tr_1", "title": "标题", "rawText": self._raw}

    def get_markdowns_by_transcript(self, _tid, mode=""):
        return [{"content": self._notes, "version": 1}] if self._notes else []


def test_update_index_marks_pending():
    """带 `vec` 时：字面索引写入 **且** 该篇被打上 `pending`（不编码）。"""
    d = tempfile.mkdtemp(prefix="limo_vec_wire_")
    try:
        eng, vs = SearchEngine(d), VectorStore(d)
        update_index(eng, _FakeStore(), "tr_1", vec=vs)
        assert len(eng.search("原文", limit=5)) == 1
        assert vs.get_state("tr_1")["status"] == "pending"
        assert vs.status()["chunks"] == 0        # 写点绝不编码
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_update_index_without_vec_unchanged():
    """不传 `vec` = 改造前的行为（默认参数，零回归）。"""
    d = tempfile.mkdtemp(prefix="limo_vec_wire0_")
    try:
        eng, vs = SearchEngine(d), VectorStore(d)
        update_index(eng, _FakeStore(), "tr_1")
        assert len(eng.search("原文", limit=5)) == 1
        assert vs.status()["docs"]["total"] == 0
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_vector_failure_does_not_kill_lexical_index():
    """向量侧抛错时，字面索引必须照常写成功（两侧各自独立 try）。

    ⚠️ 若共用一个 try，`vec.mark_pending` 抛异常会把「已经写好的字面索引」
    也报成失败 —— 用户看到「保存失败」但字面索引其实好了，重试又覆盖一次。
    """
    d = tempfile.mkdtemp(prefix="limo_vec_wire1_")
    try:
        eng = SearchEngine(d)

        class _Boom:
            def mark_pending(self, _tid):
                raise RuntimeError("向量库挂了")

        update_index(eng, _FakeStore(), "tr_1", vec=_Boom())   # ← 不许抛
        assert len(eng.search("原文", limit=5)) == 1
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_chunk_hash_stable_and_discriminating():
    """块级 hash：同文本稳定、异文本不同 —— 增量判定的全部依据。

    ⚠️ 若 hash 只取前 N 个字符之类，长块尾部改动会被判「没变」⇒ 静默不更新。
    """
    assert chunk_hash("同样的内容") == chunk_hash("同样的内容")
    assert chunk_hash("内容A") != chunk_hash("内容B")
    long_a = "开头" + "x" * 500 + "结尾A"
    long_b = "开头" + "x" * 500 + "结尾B"
    assert chunk_hash(long_a) != chunk_hash(long_b)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
