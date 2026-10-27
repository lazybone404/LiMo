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


class _WarmEngine(_FakeEngine):
    """带 `warmup` 钩子的假引擎 —— 本地引擎的钩子形状（见 `engines/embedding_local.py`）。

    用子类而不是给 `_FakeEngine` 加方法：云端引擎**没有**这个钩子，而 `_warm` 的
    `getattr` 分支正是为这一点存在的（给基类加上就等于所有测试都只在有钩子的那条路上跑）。
    """

    def __init__(self, *a, warm_error=None, **kw):
        super().__init__(*a, **kw)
        self.warm_calls = 0
        self.warm_error = warm_error

    async def warmup(self):
        self.warm_calls += 1
        if self.warm_error is not None:
            raise self.warm_error


class _LocalEngine(_WarmEngine):
    """本地引擎的**完整**钩子形状：`warmup` + `release`。

    同 `_WarmEngine` 用子类而不是给基类加方法 —— `release` 的消费方同样走 `getattr`，
    而云端引擎没有这个钩子（它的「模型」在服务端，本机一个字节都不占）。
    """

    def __init__(self, *a, release_ret=True, **kw):
        super().__init__(*a, **kw)
        #: 每次调用的 kwargs。空 dict = 用引擎自己的默认空闲门槛（idle 档要的正是这个）
        self.releases: list[dict] = []
        self._release_ret = release_ret

    def release(self, **kw):
        self.releases.append(kw)
        return self._release_ret


class _FailOnceEngine(_FakeEngine):
    """第一次 `embed` 必失败、之后正常 —— 造「坏一轮、下一轮自愈」的场景。"""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._failed = False

    async def embed(self, texts):
        if not self._failed:
            self._failed = True
            self.calls.append(list(texts))
            raise RuntimeError("第一次必失败")
        return await super().embed(texts)


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


# ── 引擎预热（本地模型的 3–5 秒加载） ──

def test_warmup_called_once_per_worker():
    """预热只做一次 —— 每轮都重载模型会让「60s 轮询」变成每 60s 卡 3–5 秒。"""
    eng = _WarmEngine()
    with _setup({"t1": _p("A")}, engine=eng) as (w, vs, _eng, _store):
        asyncio.run(w.run_once())
        asyncio.run(w.run_once())
        assert eng.warm_calls == 1
        assert vs.get_state("t1")["status"] == "ok"


def test_warmup_failure_stops_without_marking_failed():
    """⚠️ 预热失败**绝不进 `fail_count`**。

    依赖没装是「这台机器跑不了」，不是「这一篇编不出来」。记进闸门的话，3 篇之后
    整库停摆，而真正的原因只留在最后三篇的 `error` 字段里 —— 一个把「装个依赖」
    显示成「3 篇文档编码失败」的分类错误（`MAX_FAIL` 是按篇设计的）。
    """
    eng = _WarmEngine(warm_error=RuntimeError("onnxruntime 未安装"))
    with _setup({"t1": _p("A")}, engine=eng) as (w, vs, _eng, _store):
        stats = asyncio.run(w.run_once())

        assert stats["stopped"] and "加载失败" in stats["stopped"]
        assert stats["failed"] == 0                          # 不是「篇失败」
        st = vs.get_state("t1")
        assert st["failCount"] == 0                          # 闸门没被消耗
        assert st["status"] == "pending"                     # 这篇压根没被碰过
        assert eng.calls == []                               # 一次编码都没发


def test_warmup_retried_after_failure():
    """失败不置 `_warmed` —— 依赖此后装上时，下一轮还有机会恢复。

    ⚠️ 反过来（失败也置位）的失效形态是静默的：用户装了 onnxruntime、重启都不用，
    而 worker 永远认为自己「已经预热过了」，第一次查询照旧超时。
    """
    eng = _WarmEngine(warm_error=RuntimeError("暂时不行"))
    with _setup({"t1": _p("A")}, engine=eng) as (w, vs, _eng, _store):
        assert asyncio.run(w.run_once())["stopped"]

        eng.warm_error = None
        stats = asyncio.run(w.run_once())
        assert stats == {"indexed": 1, "skipped": 0, "failed": 0, "stopped": None}
        assert eng.warm_calls == 2
        assert vs.get_state("t1")["status"] == "ok"


# ── 模型内存释放（`search.vector_release` 三档） ──

def test_never_策略一次都不释放():
    """`never` 档：连 `release()` 都不该被调用（每次搜索都是热的，代价是常驻）。"""
    eng = _LocalEngine()
    with _setup({"t1": _p("A")}, engine=eng, release="never") as (w, vs, _eng, _store):
        asyncio.run(w.run_once())
        assert w.maybe_release() is False
        assert eng.releases == []


def test_idle_策略把空闲门槛交给引擎():
    """`idle` 档调 `release()` **不传 `after_idle`** —— 门槛是引擎的属性。

    反向失效形态：worker 自己写死一个门槛（比如 60s），哪天本地模型换个更小的权重、
    该动那个数时，改的是引擎、生效的是 worker，两端看起来都对。那正是模式 #54。
    """
    eng = _LocalEngine()
    with _setup({"t1": _p("A")}, engine=eng, release="idle") as (w, vs, _eng, _store):
        assert w.maybe_release() is True
        assert eng.releases == [{}], "不该传门槛（否则就是第二份判据）"


def test_immediate_只在建过东西之后释放一次():
    """`immediate` 档的两条语义缺一不可。

    - **空转轮不释放**：用户搜索时把模型拉起来了，下一个 60 秒空转轮把它踢掉 ⇒
      每次搜索都冷启动，而用户选 `immediate` 只期望「建库完放掉」这一个代价。
    - **放完就复位**：不复位的话每一轮都会再放一次（虽然第二次已无模型可放，
      但「一次性」这件事就没人钉着了）。
    """
    eng = _LocalEngine()
    with _setup({"t1": _p("A")}, engine=eng, release="immediate") as (w, vs, _eng, _store):
        assert w.maybe_release() is False, "空转轮不应释放"
        assert eng.releases == []

        w._pending_release = True             # ← 平时由 `loop` 在「本轮有推进」时置位
        assert w.maybe_release() is True
        assert eng.releases == [{"after_idle": 0.0}], "immediate 是「立刻」，不是默认门槛"
        assert w._warmed is False, "释放后必须回落到「需要重新预热」"
        assert w.maybe_release() is False, "一次性：放完复位"


def test_loop_建完之后真的释放():
    """`_pending_release` 的置位发生在 `loop` 里 —— 这条钉的是那条联动。

    直调 `maybe_release` 的用例覆盖不到它：漏了置位，`immediate` 档就是
    「配置改了、界面写着建完即释放、实际永远不释放」的静默失效。
    """
    eng = _LocalEngine()
    # `threshold=1`：这条测的是「建完之后真的释放」，不是攒批闸门。默认阈值 100
    # 会让单篇的测试库永远攒不够 —— 那是**另一条**要单独钉的语义。
    with _setup({"t1": _p("A")}, engine=eng, release="immediate",
                threshold=1) as (w, vs, _eng, _store):
        stop = asyncio.Event()

        async def _drive():
            task = asyncio.create_task(w.loop(stop, interval=5.0))
            for _ in range(50):                              # 最多等 0.5s
                if (vs.get_state("t1") or {}).get("status") == "ok":
                    break
                await asyncio.sleep(0.01)
            stop.set()
            await asyncio.wait_for(task, timeout=2)

        asyncio.run(_drive())
        assert vs.get_state("t1")["status"] == "ok"
        assert eng.releases == [{"after_idle": 0.0}], eng.releases


def test_云端引擎没有释放钩子时静默不做():
    """`_FakeEngine` 是云端的形状（只有 `embed`）：不抛、也不假装释放了。"""
    eng = _FakeEngine()
    with _setup({"t1": _p("A")}, engine=eng, release="immediate") as (w, vs, _eng, _store):
        asyncio.run(w.run_once())
        w._pending_release = True
        assert w.maybe_release() is False
        assert w._warmed is True, "什么都没释放，就不该让下一轮重新预热"


def test_空队列不预热模型():
    """⚠️ 预热排在「确认有活」之后：队列空着时加载 22.8MB 纯属浪费。

    在 `immediate` / `idle` 档下更是**每 60 秒加载一次再释放一次**（CPU 与内存来回抖），
    而 `never` 档下是「启动后白占 90MB 直到退出」。
    """
    eng = _LocalEngine()
    with _setup({}, pending=False, engine=eng) as (w, vs, _eng, _store):
        stats = asyncio.run(w.run_once())
        assert stats == {"indexed": 0, "skipped": 0, "failed": 0, "stopped": None}
        assert eng.warm_calls == 0


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
    """循环里真的会推进队列（并且 `stop` 能中断睡眠）。

    `threshold=1`：闸门本身由下面「调度」那组用例单独钉住，这条只管「循环能动」。
    """
    with _setup({"t1": _p("A")}, threshold=1) as (w, vs, eng, _store):
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


# ── 调度：攒批闸门 / 手动档 / 立即建立 ──

def test_auto_档攒不够阈值就空转且不加载模型():
    """核心判据：**达不到阈值时连模型都不该加载**。

    「模型只在建索引时加载」不是约定，是结构保证 —— 闸门排在 `_next_ids` / `_warm`
    之前。⚠️ 这条同时钉住 `_warm` 一次都没被调用：只断言「没编码」是不够的，
    实现若把闸门挪到 `_warm` 之后，编码数照样是 0（队列被取走了但没处理），
    而模型已经被白载入一次。
    """
    eng = _LocalEngine()
    docs = {f"t{i}": _p("A") for i in range(3)}
    with _setup(docs, engine=eng, threshold=100) as (w, vs, _eng, _store):
        stats = asyncio.run(w.run_once(force=False))
        assert stats == {"indexed": 0, "skipped": 0, "failed": 0, "stopped": None}
        assert eng.calls == [], "没到阈值就一次编码都不该发"
        assert eng.warm_calls == 0, "⚠️ 模型都不该加载 —— 这才是「只在重建时加载」"
        assert vs.get_state("t0")["status"] == "pending", "标记要留着，攒够了再建"


def test_auto_档攒够阈值就跑():
    eng = _LocalEngine()
    docs = {f"t{i}": _p("A") for i in range(3)}
    with _setup(docs, engine=eng, threshold=3) as (w, vs, _eng, _store):
        stats = asyncio.run(w.run_once(force=False))
        assert stats["indexed"] == 3
        assert vs.get_state("t0")["status"] == "ok"


def test_manual_档永不自动跑():
    """即使远超阈值也不动 —— 用户选的就是「我说了才建」。"""
    eng = _LocalEngine()
    docs = {f"t{i}": _p("A") for i in range(5)}
    with _setup(docs, engine=eng, trigger="manual", threshold=1) as (w, vs, _eng, _store):
        stats = asyncio.run(w.run_once(force=False))
        assert stats["indexed"] == 0
        assert eng.warm_calls == 0
        assert vs.get_state("t0")["status"] == "pending"


def test_manual_档下_force_仍能推完():
    """「立即建立索引」在 manual 档下是**唯一**通路，所以 `force` 必须穿透闸门。"""
    docs = {f"t{i}": _p("A") for i in range(3)}
    with _setup(docs, trigger="manual", threshold=1000) as (w, vs, eng, _store):
        stats = asyncio.run(w.run_once(force=True))
        assert stats["indexed"] == 3
        assert vs.get_state("t0")["status"] == "ok"


def test_重试不受阈值约束():
    """已失败过一次的篇目是**纠错**不是增长：攒不到 100 篇也该重试。

    否则 `failed` 会在界面上挂着「失败 1」直到用户碰巧导入 99 篇新文档 ——
    一个可自愈的失败被拖成「这功能坏了」。
    """
    eng = _FailOnceEngine()
    docs = {f"t{i}": _p("A") for i in range(3)}
    with _setup(docs, engine=eng, threshold=100) as (w, vs, _eng, _store):
        asyncio.run(w.run_once(force=True))                  # 第一块失败，其余成功
        assert vs.get_state("t0")["status"] == "failed"
        assert vs.get_state("t1")["status"] == "ok"
        # 阈值仍是 100（远远不够），但 retry 那一路必须放行
        stats = asyncio.run(w.run_once(force=False))
        assert stats["indexed"] == 1, "失败过的那篇重试不该被攒批闸门挡住"
        assert vs.get_state("t0")["status"] == "ok"


def test_actionable_counts_不受批次上限影响():
    """⚠️ 阈值判据不能拿 `take_pending(limit)` 的长度代替。

    `take_pending` 默认只取 20 条，攒到 500 篇时它也只会说 20 —— 阈值设成 100
    就**永远触发不了**，而界面上「待索引 500」看起来一切正常。
    """
    docs = {f"t{i}": _p("A") for i in range(45)}
    with _setup(docs, batch_docs=20) as (w, vs, _eng, _store):
        pending, retry = vs.actionable_counts()
        assert (pending, retry) == (45, 0), "计数必须是全量，不是一批的条数"
        assert len(vs.take_pending(20)) == 20, "对照组：批次取数本来就该受限"
        assert w._should_run() is False, "45 < 100，闸门该说不"


def test_request_sync_叫醒循环并跳过闸门():
    """「立即建立索引」= 置旗标 + 叫醒，**不是**自己 `await run_once()`。

    这条同时钉住两件事：
    1. 叫醒是**立刻**的（不等 `interval`，这里给 60 秒）；
    2. 它跳过了攒批闸门（阈值 1000，库里只有 1 篇）。
    """
    docs = {"t1": _p("A")}
    with _setup(docs, engine=_LocalEngine(), threshold=1000) as (w, vs, eng, _store):
        stop = asyncio.Event()

        async def _drive():
            task = asyncio.create_task(w.loop(stop, interval=60.0))
            await asyncio.sleep(0.15)                        # 让第一轮空转完、进入睡眠
            assert vs.get_state("t1")["status"] == "pending", "闸门先挡住了"
            w.request_sync()                                 # 用户点了「立即建立索引」
            for _ in range(100):                             # 最多等 1s
                if vs.get_state("t1")["status"] == "ok":
                    break
                await asyncio.sleep(0.01)
            stop.set()
            await asyncio.wait_for(task, timeout=2)

        asyncio.run(_drive())
        assert vs.get_state("t1")["status"] == "ok", "被叫醒后必须立刻跑，不等 60 秒"
        assert eng.warm_calls == 1


def test_mark_pending_会叫醒_worker():
    """写点的唤醒通路：`mark_pending` → 注册的回调。**锁外**调用、异常吞掉。

    ⚠️ 只断言「回调被调过」是不够的 —— 还要断言**回调抛异常时 `mark_pending`
    照样成功**：叫不醒 worker 只是慢一点，而让异常冒出去会把「标记」这一步整个
    弄失败，那才是真的丢索引。
    """
    docs = {"t1": _p("A")}
    with _setup(docs, pending=False) as (_w, vs, _eng, _store):
        hits: list[int] = []
        vs.set_wake_callback(lambda: hits.append(1))
        vs.mark_pending("t1")
        assert hits == [1]
        vs.mark_pending("t1")
        assert hits == [1, 1], "重复标记也要叫 —— 内容可能刚变过"

        def _boom():
            raise RuntimeError("回调炸了")

        vs.set_wake_callback(_boom)
        vs.mark_pending("t1")                                # 不抛
        assert vs.get_state("t1")["status"] == "pending", "标记本身必须成功"

        vs.set_wake_callback(None)                           # 摘掉之后不再叫
        vs.mark_pending("t1")
        assert len(hits) == 2


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
