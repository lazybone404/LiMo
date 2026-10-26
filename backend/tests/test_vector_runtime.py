"""`vector_runtime` 的编排测试 —— 「开关 / 策略 → worker + 融合层 + 模型内存」。

**为什么需要这个文件**：`vector_runtime` 是向量子系统唯一一处「什么时候该跑」的
判据，而它此前**一条测试都没有**。它的失效形态全都**不报错**：

- 关掉开关但 worker 还在跑 → 继续花钱，界面显示「已关闭」
- 改了策略但 worker 没重建 → 界面上「已保存」，行为一点没变
- 关掉开关但模型留在内存里 → 那几十 MB 一直不还（`never` 档的用户会这样以为
  「反正我要常驻」，但他已经不用这个功能了）
- 策略没变却每次都重启 worker → 丢掉 `attempted`、重跑一次对账

⚠️ 本文件用 `SimpleNamespace` 造 app、用假引擎记**每一次 `release()` 的入参**。
「释放了吗」不能靠 `release` 被调过来判 —— `idle` 档每轮都调它，由引擎自己按
闲置门槛决定放不放（`embedding_local.DEFAULT_IDLE_RELEASE`）。所以假引擎只把
**`after_idle=0.0`** 那次算真释放，其余返回 False（模拟「还在闲置期内」）。

对照实验（均实测）：
  A. `_stop_worker` 的 `if release_model` 改成 `if False`（或删掉那段）
     → `test_关掉开关会释放模型` / `test_never_档关掉开关也释放` 红
  B. 把六条 `_stop_worker(app, release_model=True)` 里的 `release_model=True` 全删
     → 同上两条红
  C. `stop_vector_runtime` 里改成 `release_model=True`
     → `test_重建路径不释放模型` 红（而它是「重建完马上要编码」的正确前提）
  D. `_ensure_worker` 里那处也改成 `release_model=True`
     → `test_策略变化重启但不释放模型` 红
  E. `_stop_worker` 里删掉 `app.state.vector_worker = None`
     → `test_关掉开关清干净两个引用` 红（目前无读者，所以这条是防未来的）
  F. `_ensure_worker` 去掉「策略不同则重启」的分支（回到「已在跑就不动」）
     → `test_策略变化真的重启` 红
"""
import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.core.registry import EngineRegistry
from app.core.vector_store import DEFAULT_CHUNK_SIZE
from app.services import vector_runtime as vr

_UNSET = object()


class _Engine:
    """本地引擎形状：有 `release` 钩子，闲置门槛由它自己判。"""

    name = "local-x"
    display_name = "local-x（本地）"
    model_id = "local-x-model"
    dimension = 512
    is_local = True

    def __init__(self):
        #: 每次 `release()` 的 `after_idle` 入参（没传记 `_UNSET`）
        self.calls: list = []

    def release(self, *args, **kwargs):
        after_idle = kwargs.get("after_idle", _UNSET)
        self.calls.append(after_idle)
        if after_idle is _UNSET:
            return False          # 走引擎默认门槛：模拟「刚用过，还在闲置期内」
        return after_idle == 0.0  # 显式 0 = 「立刻放」

    def freed_now(self) -> bool:
        """真的释放过吗（`after_idle=0.0` 那次）。"""
        return 0.0 in self.calls


class _Vec:
    """假 `VectorStore`：只实现 `VectorWorker` 与 `sync_vector_runtime` 摸到的面。"""

    available = True

    def __init__(self):
        self.identity = None
        self.ensure_ok = True

    def ensure_identity(self, model_id, dimension, chunk_size):
        self.identity = (model_id, dimension, chunk_size)
        return self.ensure_ok

    def take_pending(self, n):
        return []

    def retry_ids(self, *a):
        return []

    def known_ids(self):
        return set()

    def mark_pending(self, tx_id):
        pass

    def mark_failed(self, *a):
        pass

    def delete_doc(self, tx_id):
        pass


class _Store:
    def list_transcripts(self):
        return []


def _app(cfg: dict, engine: _Engine) -> tuple[SimpleNamespace, _Vec]:
    app = SimpleNamespace(state=SimpleNamespace())
    app.state.config = {"search": dict(cfg)}
    app.state.store = _Store()
    app.state.vector_store = _Vec()
    # 预置一个「上一轮留下的」融合层：成功分支要覆盖它，停用分支要摘掉它
    app.state.vector_search = "stale"
    return app, app.state.vector_store


@pytest.fixture()
def engine(monkeypatch):
    eng = _Engine()
    # 换掉整个注册表（进程内单例，别的测试文件可能注册过真引擎 —— 不隔离的话
    # `EngineRegistry.get` 拿到的会是真本地引擎，它会真去 import onnxruntime）。
    monkeypatch.setattr(EngineRegistry, "_stores", {"embedding": {eng.name: eng}})
    return eng


def _run(coro_fn):
    return asyncio.run(coro_fn())


def test_打开开关起_worker_并装上融合层(engine):
    async def go():
        app, vec = _app({"vector_enabled": True, "vector_model": engine.name}, engine)
        try:
            r = await vr.sync_vector_runtime(app)
            assert r == {"enabled": True, "reason": None}, r
            assert app.state.vector_search is not None, "成功分支必须装上融合层"
            assert app.state.vector_task is not None and not app.state.vector_task.done()
            assert app.state.vector_worker.release == "idle"
            assert vec.identity == (engine.model_id, engine.dimension, DEFAULT_CHUNK_SIZE)
        finally:
            await vr.stop_vector_runtime(app)

    _run(go)


def test_关掉开关会释放模型(engine):
    """① 号缺陷的正题：关掉语义检索 = 用户不再用这个能力，模型该还回去。

    ⚠️ 此前 `_stop_worker` 只停 task、从不碰引擎 —— 那几十 MB 会一直占着，
    而设置页上写着「已关闭」。没有任何报错，也没有任何痕迹。
    """
    async def go():
        app, _ = _app({"vector_enabled": True, "vector_model": engine.name}, engine)
        await vr.sync_vector_runtime(app)
        assert not engine.freed_now(), "前提：刚开的时候不该释放"

        app.state.config["search"]["vector_enabled"] = False
        r = await vr.sync_vector_runtime(app)
        assert r == {"enabled": False, "reason": None}, r
        assert app.state.vector_search is None, "关掉后融合层必须摘掉"
        assert engine.freed_now(), f"关掉开关必须释放模型，实际调用 {engine.calls}"

    _run(go)


def test_关掉开关清干净两个引用(engine):
    """② `vector_task` 与 `vector_worker` 都要清。

    只清 `vector_task` 不会错（`_ensure_worker` 读的就是它），但留下一个指向
    已死 task 的 `vector_worker` 就是定时炸弹：将来任何按它读 `worker.release`
    或 `worker.stats` 的代码都会拿到上一实例的陈旧值，而不会有任何报错。
    """
    async def go():
        app, _ = _app({"vector_enabled": True, "vector_model": engine.name}, engine)
        await vr.sync_vector_runtime(app)
        assert app.state.vector_worker is not None

        app.state.config["search"]["vector_enabled"] = False
        await vr.sync_vector_runtime(app)
        assert app.state.vector_task is None
        assert app.state.vector_worker is None
        assert app.state.vector_stop is None

    _run(go)


def test_never_档关掉开关也释放(engine):
    """`never` 是「**平时**常驻」，不是「关掉之后还占着」。

    ⚠️ 这里刻意绕开策略（`VectorWorker.release_now`）：用 `never` 挡住释放，
    等于「选了常驻内存的用户关掉语义检索后，那几十 MB 再也还不回来」——
    而他表达的是「我不想为搜索等加载」，不是「我要永久占着」。
    """
    async def go():
        app, _ = _app({"vector_enabled": True, "vector_model": engine.name,
                       "vector_release": "never"}, engine)
        await vr.sync_vector_runtime(app)
        worker = app.state.vector_worker
        assert worker.release == "never"
        await asyncio.sleep(0.05)          # 让 loop 至少空转一轮
        assert engine.calls == [], "never 档平时一次都不该调 release"

        app.state.config["search"]["vector_enabled"] = False
        await vr.sync_vector_runtime(app)
        assert engine.freed_now(), f"never 档关掉功能也必须释放，实际 {engine.calls}"

    _run(go)


def test_重建路径不释放模型(engine):
    """`stop_vector_runtime` 的两个调用方都不该放：停机（无所谓）与**重建**。

    ⚠️ 重建是「先停 worker → reset_identity → 再同步」，重建完马上就是一轮全库
    编码。在这里释放等于让用户白付一次 3–5 秒的模型加载，而界面上只表现为
    「重建好像有点慢」。与上一条成对：都是「停 worker」，一个放一个不放。
    """
    async def go():
        app, _ = _app({"vector_enabled": True, "vector_model": engine.name}, engine)
        await vr.sync_vector_runtime(app)

        await vr.stop_vector_runtime(app)
        assert app.state.vector_search is None
        assert not engine.freed_now(), f"重建 / 停机路径不该释放，实际 {engine.calls}"

    _run(go)


def test_策略变化真的重启(engine):
    """只写配置不重建 worker ⇒ 用户改了设置、界面「已保存」、行为一点没变。"""
    async def go():
        app, _ = _app({"vector_enabled": True, "vector_model": engine.name}, engine)
        await vr.sync_vector_runtime(app)
        first = app.state.vector_task

        app.state.config["search"]["vector_release"] = "never"
        await vr.sync_vector_runtime(app)
        assert app.state.vector_task is not first, "策略变了必须换一个 worker"
        assert app.state.vector_worker.release == "never"
        # 对照：这次重启**不该**释放（用户刚表达「要常驻」）
        assert not engine.freed_now(), f"重启不该释放模型，实际 {engine.calls}"

    _run(go)


def test_策略没变不重启(engine):
    """重启会丢掉 `attempted`、重跑一次对账 —— 纯噪音。"""
    async def go():
        app, _ = _app({"vector_enabled": True, "vector_model": engine.name}, engine)
        await vr.sync_vector_runtime(app)
        task, worker = app.state.vector_task, app.state.vector_worker

        await vr.sync_vector_runtime(app)      # 配置一个字没动
        assert app.state.vector_task is task and app.state.vector_worker is worker

    _run(go)


def test_口径不符时释放且不自动重建(engine):
    """`ensure_identity` 为假 = 换了模型/维度/切块规则。**停用 + 释放，不重建**：
    重建要花一遍全库编码的钱，而用户完全不知道发生了什么。
    """
    async def go():
        app, vec = _app({"vector_enabled": True, "vector_model": engine.name}, engine)
        await vr.sync_vector_runtime(app)      # 先起一次，造出「模型已加载」
        vec.ensure_ok = False

        r = await vr.sync_vector_runtime(app)
        assert r["enabled"] is False and "口径" in r["reason"], r
        assert app.state.vector_search is None
        assert engine.freed_now(), f"口径不符（= 现在跑不了）也该释放，实际 {engine.calls}"

    _run(go)


def test_库不可用时释放(engine):
    async def go():
        app, vec = _app({"vector_enabled": True, "vector_model": engine.name}, engine)
        await vr.sync_vector_runtime(app)
        vec.available = False

        r = await vr.sync_vector_runtime(app)
        assert r["enabled"] is False and r["reason"] == "向量索引库不可用", r
        assert engine.freed_now()

    _run(go)


def test_引擎找不到时不停不炸(engine):
    """配置里写了一个不存在的引擎名 —— 停用 + 释放 + 原因带上那个名字。"""
    async def go():
        app, _ = _app({"vector_enabled": True, "vector_model": engine.name}, engine)
        await vr.sync_vector_runtime(app)

        app.state.config["search"]["vector_model"] = "nope"
        r = await vr.sync_vector_runtime(app)
        assert r["enabled"] is False and "nope" in r["reason"], r
        assert engine.freed_now()

    _run(go)


def test_脏策略回退_idle(engine, caplog):
    """**读回宽**：磁盘上可能有过期/手改的值，为了一个读不懂的键让整块设置报错
    毫无意义（与请求入口的 `VECTOR_BAD_RELEASE` 显式 400 刻意不同）。
    """
    assert vr._release_policy("never") == "never"
    assert vr._release_policy(None) == "idle"          # 旧配置没有这个键
    assert vr._release_policy("手改出来的值") == "idle"

    async def go():
        app, _ = _app({"vector_enabled": True, "vector_model": engine.name,
                       "vector_release": "手改出来的值"}, engine)
        try:
            await vr.sync_vector_runtime(app)
            assert app.state.vector_worker.release == "idle"
        finally:
            await vr.stop_vector_runtime(app)

    _run(go)
