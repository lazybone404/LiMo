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
from app.services.vector_worker import VectorWorker

#: 默认向量化引擎。刻意不走 `engine_bindings`（那是「文件 → 文本」的子场景管辖），
#: 与 `search.vector_model` 配置项成对 —— 前者是默认值，后者是用户覆盖。
DEFAULT_ENGINE = "dashscope-embedding"


async def sync_vector_runtime(app) -> dict:
    """按当前配置把向量子系统调到「该在的状态」。返回 `{enabled, reason}`。

    幂等：启动时调一次、每次开关调一次。**已经跑着的 worker 不重启** —— 重启会
    丢掉 `attempted` 集合、重跑一次对账，纯属噪音。
    """
    cfg = (getattr(app.state, "config", None) or {}).get("search") or {}
    vec = getattr(app.state, "vector_store", None)

    def _off(reason: str | None) -> dict:
        app.state.vector_search = None
        return {"enabled": False, "reason": reason}

    if not cfg.get("vector_enabled"):
        await _stop_worker(app)
        result = _off(None)
    elif vec is None:
        await _stop_worker(app)
        result = _off("向量索引库未初始化")
    else:
        name = cfg.get("vector_model") or DEFAULT_ENGINE
        engine = EngineRegistry.get("embedding", name)
        if engine is None:
            await _stop_worker(app)
            result = _off(f"未找到向量化引擎：{name}")
        elif not vec.available:
            await _stop_worker(app)
            result = _off("向量索引库不可用")
        elif not vec.ensure_identity(engine.model_id, engine.dimension, DEFAULT_CHUNK_SIZE):
            await _stop_worker(app)
            result = _off("向量索引口径与当前配置不符（模型 / 维度 / 切块规则变了），"
                          "需要重建向量索引后才能使用")
        else:
            app.state.vector_search = VectorSearcher(vec, engine)
            _start_worker(app, vec, engine)
            result = {"enabled": True, "reason": None}

    app.state.vector_runtime = result
    if result["reason"]:
        log.warning("语义检索未启用", reason=result["reason"])
    return result


async def stop_vector_runtime(app) -> None:
    """停机：停 worker、摘掉融合层。**不写配置**（这是临时状态，不是用户的决定）。"""
    app.state.vector_search = None
    await _stop_worker(app)


# ── worker 启停 ──

def _start_worker(app, vec, engine) -> None:
    task = getattr(app.state, "vector_task", None)
    if task is not None and not task.done():
        return                                  # 已在跑：不重启（见 `sync_vector_runtime`）
    worker = VectorWorker(app.state.store, vec, engine, chunk_size=DEFAULT_CHUNK_SIZE)
    stop = asyncio.Event()
    app.state.vector_worker = worker
    app.state.vector_stop = stop
    app.state.vector_task = asyncio.create_task(worker.loop(stop))


async def _stop_worker(app) -> None:
    stop = getattr(app.state, "vector_stop", None)
    task = getattr(app.state, "vector_task", None)
    # 先清引用再取消：`_start_worker` 的「已在跑」判据读的正是这个引用，
    # 取消与清空之间若被另一条请求插进来，它会拿到一个即将死掉的 task。
    app.state.vector_stop = None
    app.state.vector_task = None
    if stop is not None:
        stop.set()
    if task is None or task.done():
        return
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
