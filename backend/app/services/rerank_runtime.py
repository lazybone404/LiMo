"""重排子系统的运行时编排 —— 「开关 → `app.state.rerank_search`」的**唯一一处**。

形态与 `services/vector_runtime.py` 同源但简单得多：那边要管一个后台 worker 的
启停与模型释放，这边只有一个无状态的编排对象。**共用同一条纪律**：

⚠️ 为什么需要它：重排有**两个**启用来源 —— 应用启动，以及设置页在运行时打开开关。
两处各写一遍判据，迟早分叉成「一边建了 `rerank_search`、另一边只写了配置」，
而那时界面上显示「已开启」、对话检索里却一次都没重排过，两边都不报错。

⚠️ 层归属同 `vector_runtime`：本模块**只被 `main.py` 与 `routers/search.py` 调用**，
它是刻意的例外（其余 `app/services/` 模块零 FastAPI import 且不碰 `app.state`）。

⚠️ **失败一律显式**：引擎找不到 / 自检不过 → `enabled: False` + 一句给用户看的
`reason`，而不是静默降级成「没开」。判据同 `vector_runtime`：用户打开了开关却
发现没有效果时，状态页得有话可说 —— 对话工具调用的失败只会在 `tool_result`
里留一句，用户不会去翻日志。
"""

from __future__ import annotations

from app.core import logging as log
from app.core.registry import EngineRegistry
from app.services.rerank_search import RerankSearcher

#: 默认重排引擎。与 `search.rerank_model` 配置项成对 —— 前者是默认值，后者是
#: 用户覆盖。刻意不走 `engine_bindings`（那是「文件 → 文本」的子场景管辖）。
DEFAULT_ENGINE = "dashscope-rerank"


def sync_rerank_runtime(app) -> dict:
    """按当前配置把重排子系统调到「该在的状态」。返回 `{enabled, reason}`。

    幂等：启动时调一次、每次开关调一次。与 `sync_vector_runtime` 不同，这里
    **没有需要重启的后台任务**，所以没有「策略变了要不要重启」那一层 ——
    候选窗口是构造参数，每次调用都按当前配置重建一个 `RerankSearcher` 即可
    （它没有状态，重建零成本）。

    ⚠️ **`release()` 不在这里调**：`vector_runtime` 要放模型是因为本地 embedding
    常驻几十 MB；重排引擎本轮只有云端一种形态，它不占本机内存。本地 reRank
    插件（bge-reranker 数百 MB）将来若要接，释放策略应加在它自己的引擎侧，
    而不是在这里分支 —— 判据同 `embedding_local.DEFAULT_IDLE_RELEASE` 住在引擎侧。
    """
    cfg = (getattr(app.state, "config", None) or {}).get("search") or {}

    def _off(reason: str | None) -> dict:
        app.state.rerank_search = None
        return {"enabled": False, "reason": reason}

    if not cfg.get("rerank_enabled"):
        result = _off(None)
    else:
        name = cfg.get("rerank_model") or DEFAULT_ENGINE
        engine = EngineRegistry.get("rerank", name)
        if engine is None:
            result = _off(f"未找到重排引擎：{name}")
        elif (blocked := _engine_precheck(engine)) is not None:
            result = _off(blocked)
        else:
            app.state.rerank_search = RerankSearcher(
                engine, candidates=cfg.get("rerank_candidates"))
            result = {"enabled": True, "reason": None}

    app.state.rerank_runtime = result
    if result["reason"]:
        log.warning("重排未启用", reason=result["reason"])
    return result


def stop_rerank_runtime(app) -> None:
    """停机 / 测试收尾：摘掉编排对象。**不写配置**（这是临时状态，不是用户的决定）。"""
    app.state.rerank_search = None


def _engine_precheck(engine) -> str | None:
    """引擎自检 —— **可选钩子**，返回给用户看的没就绪原因（`None` = 可以跑）。

    ⚠️ `getattr` 而不是往 `RerankEngine` 协议里加方法：那个协议是
    `runtime_checkable`，加成员会让**云端引擎不再满足它**（同
    `vector_runtime._engine_precheck`、`embedding` 那三个钩子的理由）。

    ⚠️ 与 `RerankSearcher._precheck` **不是重复**：那一层在每次查询时兜底
    （配置可能刚被改、引擎可能中途坏掉），这一层在**开关那一刻**回答「为什么
    开不起来」，让状态页有话可说。两层调的是同一个钩子，判据却只有引擎里的那
    一份 —— 这里只是把结果搬到 `app.state.rerank_runtime` 上。
    """
    fn = getattr(engine, "precheck", None)
    if fn is None:
        return None
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 —— 自检自己炸了也只是「不可用」
        return f"重排引擎自检失败：{type(e).__name__}: {e}"
