"""向量子系统的运行时编排 —— 「开关 → worker + 融合层」的**唯一一处**。

⚠️ 为什么需要它：向量 worker 有**两个**启动来源 —— 应用启动流程，以及设置页在
运行时打开开关。两处各写一遍启停，就是两份「什么时候该跑」的判据；更糟的是其中
一份大概率会漏掉「把 task 引用存回 `app.state`」，于是关掉开关后 worker 还在跑，
**继续花钱**，而界面上显示「已关闭」。

⚠️ 层归属：`app/services/` 其余模块（`query.py` 等）零 FastAPI import 且不碰
`app.state`，因为 CLI 要直接 import 它们。本模块是刻意的例外，**只被 `main.py` 与
`routers/search.py` 调用** —— 它编排的就是「进程内的一个后台任务」，
这件事离开 app 生命周期没有意义。

⚠️ **口径不符时只停用、不重建**：`ensure_identity` 返回 False 意味着「模型 / 维度 /
切块规则变了」，重建要花一遍全库编码的钱。自动重建等于用户换了模型就默默扣费，
而且他完全不知道发生了什么（索引变好用了，账单变大了）。重建只能由
`POST /search/vector/rebuild` 显式发起。
"""

from __future__ import annotations

import asyncio

from app.core import logging as log
from app.core.registry import EngineRegistry
from app.core.vector_store import DEFAULT_CHUNK_SIZE
from app.services.vector_search import VectorSearcher
from app.services.vector_worker import DEFAULT_RELEASE, RELEASE_POLICIES, VectorWorker

#: 默认向量化引擎。刻意不走 `engine_bindings`（那是「文件 → 文本」的子场景管辖），
#: 与 `search.vector_model` 配置项成对 —— 前者是默认值，后者是用户覆盖。
DEFAULT_ENGINE = "dashscope-embedding"


async def sync_vector_runtime(app) -> dict:
    """按当前配置把向量子系统调到「该在的状态」。返回 `{enabled, reason}`。

    幂等：启动时调一次、每次开关调一次。**策略没变的在跑 worker 不重启** —— 重启会
    丢掉 `attempted` 集合、重跑一次对账，纯属噪音。策略变了则真的重启（见
    `_ensure_worker`：只写配置不重建 worker 的话，用户改了设置而行为一点没变）。

    ⚠️ **六条「不在用」的路径都带 `release_model=True`**：关掉开关、库未初始化、
    引擎找不到、库不可用、引擎自检不过、口径不符 —— 它们对用户的共同含义是
    「语义检索现在不工作」，那模型就没有留着的理由（尤其 `never` 档的用户，
    他选的是「**平时**常驻」，不是「关掉之后还占着」）。
    """
    cfg = (getattr(app.state, "config", None) or {}).get("search") or {}
    vec = getattr(app.state, "vector_store", None)
    release = _release_policy(cfg.get("vector_release"))

    def _off(reason: str | None) -> dict:
        app.state.vector_search = None
        return {"enabled": False, "reason": reason}

    if not cfg.get("vector_enabled"):
        await _stop_worker(app, release_model=True)
        result = _off(None)
    elif vec is None:
        await _stop_worker(app, release_model=True)
        result = _off("向量索引库未初始化")
    else:
        name = cfg.get("vector_model") or DEFAULT_ENGINE
        engine = EngineRegistry.get("embedding", name)
        if engine is None:
            await _stop_worker(app, release_model=True)
            result = _off(f"未找到向量化引擎：{name}")
        elif not vec.available:
            await _stop_worker(app, release_model=True)
            result = _off("向量索引库不可用")
        elif (blocked := _engine_precheck(engine)) is not None:
            # ⚠️ 必须排在 `ensure_identity` **之前**：换引擎（1024 维云端 → 512 维本地）
            # 时口径**必然**不符，若这里放行，用户会先被指去点「重建向量索引」，
            # 而重建同样会因缺依赖失败 —— 一次白跑的计费动作 + 一句误导的原因。
            await _stop_worker(app, release_model=True)
            result = _off(blocked)
        elif not vec.ensure_identity(engine.model_id, engine.dimension, DEFAULT_CHUNK_SIZE):
            await _stop_worker(app, release_model=True)
            result = _off("向量索引口径与当前配置不符（模型 / 维度 / 切块规则变了），"
                          "需要重建向量索引后才能使用")
        else:
            app.state.vector_search = VectorSearcher(vec, engine)
            await _ensure_worker(app, vec, engine, release)
            result = {"enabled": True, "reason": None}

    app.state.vector_runtime = result
    if result["reason"]:
        log.warning("语义检索未启用", reason=result["reason"])
    return result


async def stop_vector_runtime(app) -> None:
    """停机：停 worker、摘掉融合层。**不写配置**（这是临时状态，不是用户的决定）。

    ⚠️ **刻意不释放模型**（`release_model=False`）：两个调用方都不该放 ——
    进程停机时放不放没有区别；而「重建向量索引」正要用它（`POST /search/vector/rebuild`
    先停 worker 再重建，重建完马上就是一轮全库编码）。这里放掉等于让重建白付一次加载。
    真正该释放的是 `sync_vector_runtime` 的六条「关掉 / 不可用」路径。
    """
    app.state.vector_search = None
    await _stop_worker(app)


def _release_policy(raw) -> str:
    """配置里的释放策略 → 合法值。**读回宽**：缺失（旧配置没有这个键）与手改出来的
    非法值一律回退默认，不抛 —— 与请求入口的 `VECTOR_BAD_RELEASE` 显式 400 **刻意不同**：
    那里是「用户刚选的值」，这里是「磁盘上可能过期的值」。退回默认在语义上是安全的
    （`idle` 只是让模型闲置后不占内存），而拒绝启动一个新装的小功能毫无意义。
    """
    if raw in RELEASE_POLICIES:
        return raw
    if raw is not None:
        log.warning("未知的向量释放策略，已回退默认", value=str(raw)[:40],
                    fallback=DEFAULT_RELEASE)
    return DEFAULT_RELEASE


def _engine_precheck(engine) -> str | None:
    """引擎自检 —— **可选钩子**，返回给用户看的没就绪原因（`None` = 可以跑）。

    ⚠️ 为什么是 `getattr` 而不是往 `EmbeddingEngine` 协议里加方法：那个协议是
    `runtime_checkable`，加成员会让**云端引擎不再满足它**（除非也补一个空实现），
    而云端确实没有「依赖装没装」这回事（Key 的问题在第一次编码时才该显式报）。
    本地引擎用它回答「onnxruntime 装了吗 / 模型文件在吗」。

    ⚠️ 与 `VectorSearcher._precheck`（库不可用 / 没建过 / 口径不符）**分层**：
    那里的三档都是「用户点一下重建或等后台建完」，这里的这一档是「去装依赖」。
    合并成一句会让人无从下手。
    """
    fn = getattr(engine, "precheck", None)
    if fn is None:
        return None
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 —— 自检自己炸了也只是「不可用」
        return f"向量化引擎自检失败：{type(e).__name__}: {e}"


# ── worker 启停 ──

async def _ensure_worker(app, vec, engine, release: str) -> None:
    """worker 该在就在、该重启就重启。**唯一一处**把策略送进 `VectorWorker`。

    ⚠️ **策略变了必须真的重启**：`loop` 每轮读的是 `worker.release`，只写配置
    不重建 worker 的话，用户改了设置、界面上「已保存」、行为却一点没变 —— 一个
    零报错、也零效果的失效形态。而 `_start_worker` 原本的「已在跑就不动」正是
    为此加了一层判断：**它挡的是「每次开会话都重启」的噪音，不是「策略变了也不动」**。
    """
    task = getattr(app.state, "vector_task", None)
    worker = getattr(app.state, "vector_worker", None)
    if task is not None and not task.done():
        if worker is not None and worker.release == release:
            return                              # 已在跑且策略未变：不重启
        # ⚠️ **重启不释放模型**（默认 `release_model=False`）：用户可能刚把
        # `immediate` 改成 `never`（明确要它常驻），这次重启正是为落实那个选择 ——
        # 顺手放掉模型与他的意愿正好相反，而且下一次编码还要白付一次加载。
        await _stop_worker(app)
    _start_worker(app, vec, engine, release)


def _start_worker(app, vec, engine, release: str) -> None:
    worker = VectorWorker(app.state.store, vec, engine,
                          chunk_size=DEFAULT_CHUNK_SIZE, release=release)
    stop = asyncio.Event()
    app.state.vector_worker = worker
    app.state.vector_stop = stop
    app.state.vector_task = asyncio.create_task(worker.loop(stop))


async def _stop_worker(app, *, release_model: bool = False) -> None:
    """停掉 worker。`release_model=True` 时**连模型一起放掉**。

    ⚠️ **两个引用都要清**（`vector_task` 与 `vector_worker`）：`_ensure_worker` 的
    「已在跑」判据读的是 `vector_task`，虽然只清它也不会错，但留下一个指向已死
    task 的 `vector_worker` 就是一颗定时炸弹 —— 任何将来按它读 `worker.release`
    或 `worker.stats` 的代码都会拿到上一个实例的陈旧值，而不会有任何报错。

    ⚠️ `release_model` **不是「顺便」而是调用方的语义**：关掉语义检索 = 用户不再用
    这个能力，那几十 MB 该还回去（`never` 策略也挡不住 —— 见 `VectorWorker.release_now`）。
    而「重启 worker / 重建索引」不该放，见 `_ensure_worker` 与 `stop_vector_runtime`。
    """
    stop = getattr(app.state, "vector_stop", None)
    task = getattr(app.state, "vector_task", None)
    worker = getattr(app.state, "vector_worker", None)
    # 先清引用再取消：`_start_worker` 的「已在跑」判据读的正是这个引用，
    # 取消与清空之间若被另一条请求插进来，它会拿到一个即将死掉的 task。
    app.state.vector_stop = None
    app.state.vector_task = None
    app.state.vector_worker = None
    if stop is not None:
        stop.set()
    if task is not None and not task.done():
        # ⚠️ `set()` 与 `cancel()` **两个都要**：`set()` 让循环在下一个等待点自己退出；
        # `cancel()` 兜住「正卡在一次 embedding 往返里」的情形 —— 那次等待最长 60s，
        # 不 cancel 的话关闭窗口要等它，而 uvicorn 会先打印「等待后台任务超时」。
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001 —— 停机路径不该因为 worker 的异常而起不来
            log.warning("向量索引 worker 退出异常", err=f"{type(e).__name__}: {e}"[:200])
    # ⚠️ **释放排在 await 之后**：worker 活着的时候可能正用着这份句柄，
    # 先放再 cancel 会让那一次编码在半路失去模型（在 ONNX 上表现为崩溃而非异常）。
    if release_model and worker is not None:
        worker.release_now()
