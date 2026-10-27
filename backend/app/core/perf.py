"""性能打点 —— 只记录，不判断。

三条设计纪律：

1. **不做判断。** 没有「慢」阈值，没有采样率。快样本与慢样本一样落盘 ——
   阈值应该由看数据的人根据分位数决定，而不是由写代码的人猜一个 2000ms
   写死在中间件里（改造前 `_log_request` 就是这么干的：≥2000ms 才 WARN，
   于是 1900ms 的退化和 12ms 的正常请求在数据上长得一模一样）。

2. **写失败绝不影响业务。** perf.log 写不进去（磁盘满 / 权限 / 被占用）时只记
   一次 warning，之后静默。打点是诊断设施，不是功能。

3. **独立文件。** 落 `{data_dir}/perf.log`，按天滚动保留 14 天，不进 app.log、
   不进 `GET /api/logs` 的内存环形缓冲。app.log 是**业务**日志（5×2MB 滚动），
   性能行混进去既挤占它、又没法直接算分位数；内存缓冲只有 1000 条，几秒就能被
   性能行刷空，日志面板会看不见真正的业务日志。

HTTP 那一层由 `main.py::add_request_id` 中间件横切（每个请求一条），本模块只提供
`record` / `timer` / `snapshot`。**没有请求边界的地方才需要手动 `timer()`** ——
目前只有三处：启动加载、`Store._save_index`、向量 worker 一轮。

⚠️ **流式响应（SSE）的耗时不是流的总时长。** `BaseHTTPMiddleware` 的 `call_next`
要等到 `http.response.start` 才返回，所以它量到的是**建连（TTFB）**。
2026-10-27 实测：app.log 里所有 `/api/generate/{id}/stream` 都是 2–14ms，
而生成实际跑几十秒。故调用方（`_log_request`）给 SSE 的 key 加 ` [ttfb]` 后缀，
避免这些 2ms 样本把普通请求的分位数整体拉低。

⚠️ **路径必须归一化后再当 key。** 中间件用 `request.scope["route"].path`（路由模板，
`/transcripts/{tx_id}`）而不是原始 path —— 否则几千篇文档就是几千个统计组，
`snapshot()` 会返回一张没人看得懂的表。注意 FastAPI 0.139 的 `route.path` **不含**
`include_router(prefix="/api")` 的前缀（见 `main.py::_log_request` 的注释），故那里
统一剥掉 `/api`，让「已匹配路由」与「未匹配路由（退回原始 path）」两种来源口径一致。
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections import deque
from contextlib import contextmanager
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any, Iterator

from app.core import logging as log

#: 每个分组保留的样本数。取 500 是因为 P95 需要足够样本才有意义
#: （500 个样本时 P95 落在第 475 个），而 500×~40 个分组也只有几 MB。
_SAMPLE_CAP = 500

#: 分组硬上限。路由模板归一化后组数是有限且稳定的，这条只是防御
#: 「某个动态路径没归一化好」时不会无限增长吃内存。
_MAX_GROUPS = 200

#: 保留天数（TimedRotatingFileHandler 的 backupCount）
_KEEP_DAYS = 14

_samples: dict[str, deque[float]] = {}
_samples_lock = threading.Lock()
_write_failed = False
_handler: logging.Handler | None = None
_log_dir: Path | None = None


# ── 落盘 ──

def setup(log_dir: str | Path) -> None:
    """挂上 perf.log 的滚动文件处理器（幂等）。

    在 `main.py::startup` 里、`Store()` 构造**之前**调用 —— 否则启动加载那一条
    测不到（那恰恰是最容易退化的地方：索引一坏就要重建全库）。

    幂等：重复调用不重复挂 handler（测试里会反复 setup）。
    """
    global _handler, _log_dir, _write_failed
    if _handler is not None:
        return

    d = Path(log_dir)
    try:
        d.mkdir(parents=True, exist_ok=True)
        # delay=True：没有第一次写入就不建文件（全新用户不该凭空多一个空文件）
        h = TimedRotatingFileHandler(
            str(d / "perf.log"),
            when="midnight",
            backupCount=_KEEP_DAYS,
            encoding="utf-8",
            delay=True,
        )
        h.setFormatter(logging.Formatter("%(message)s"))  # 纯 JSON Lines
        h.setLevel(logging.INFO)

        lg = logging.getLogger("limo.perf")
        lg.setLevel(logging.INFO)
        lg.propagate = False  # 不进 root → 不污染 app.log / 控制台 / 内存缓冲
        lg.addHandler(h)
        _handler = h
        _log_dir = d  # 只在真的挂上之后才对外声称有落盘路径（失败时 snapshot 如实为 None）
        _write_failed = False
    except OSError as e:
        log.warning("性能打点落盘不可用（仅保留进程内统计）", err=f"{type(e).__name__}: {e}"[:200])


def _write(name: str, ms: float, fields: dict[str, Any]) -> None:
    """写一行 JSONL。任何失败都只报一次，之后静默。"""
    global _write_failed
    if _handler is None or _write_failed:
        return
    rec: dict[str, Any] = {"ts": round(time.time(), 3), "name": name, "ms": round(ms, 1)}
    for k, v in fields.items():
        if v is not None:
            rec[k] = v
    try:
        logging.getLogger("limo.perf").info(json.dumps(rec, ensure_ascii=False))
    except Exception as e:  # noqa: BLE001 —— 打点绝不能把业务带崩
        _write_failed = True
        log.warning("性能打点写入失败（本次进程内不再尝试）", err=f"{type(e).__name__}: {e}"[:200])


# ── 记录 ──

def record(name: str, ms: float, **fields: Any) -> None:
    """记一条打点：进程内统计 + 落盘。

    `name` 是分组键。HTTP 用「方法 + 路由模板」（`GET /api/transcripts/{tx_id}`），
    非 HTTP 用固定名（`startup.store` / `store.save_index` / `vector.worker_round`）。
    """
    # 进程内统计（供 GET /api/perf）。先统计后落盘：写盘失败不该让分位数也丢。
    with _samples_lock:
        dq = _samples.get(name)
        if dq is None:
            if len(_samples) >= _MAX_GROUPS:
                return  # 组数超限：只丢落盘与统计，不影响业务
            dq = deque(maxlen=_SAMPLE_CAP)
            _samples[name] = dq
        dq.append(ms)
    _write(name, ms, fields)


@contextmanager
def timer(name: str, **fields: Any) -> Iterator[None]:
    """给没有请求边界的地方计时（`with perf.timer("store.save_index"):`）。

    ⚠️ 异常路径同样记录 —— 「这一步抛了、抛之前花了 8 秒」正是最需要的那条数据。
    """
    t0 = time.perf_counter()
    try:
        yield
    finally:
        record(name, (time.perf_counter() - t0) * 1000, **fields)


# ── 读取 ──

def _pct(sorted_vals: list[float], q: float) -> float:
    """nearest-rank 分位数。样本少时如实返回，不插值（不假装知道得更精确）。"""
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, max(0, math.ceil(q * len(sorted_vals)) - 1))
    return round(sorted_vals[idx], 1)


def snapshot() -> dict:
    """进程内统计，按 p95 降序（最该看的排最前）。

    ⚠️ 这是**本进程**的数据，重启即清零。要跨会话看趋势请读 `{data_dir}/perf.log`
    （JSON Lines，按天滚动保留 14 天）。
    """
    with _samples_lock:
        items = [(k, list(v)) for k, v in _samples.items()]

    groups = []
    for name, vals in items:
        vals.sort()
        groups.append({
            "name": name,
            "count": len(vals),
            "min": round(vals[0], 1),
            "p50": _pct(vals, 0.50),
            "p95": _pct(vals, 0.95),
            "max": round(vals[-1], 1),
            "avg": round(sum(vals) / len(vals), 1),
        })
    groups.sort(key=lambda g: (-g["p95"], -g["count"]))

    return {
        "groups": groups,
        "totalSamples": sum(len(v) for _, v in items),
        "logFile": str(_log_dir / "perf.log") if _log_dir else None,
        "keepDays": _KEEP_DAYS,
    }


def reset() -> None:
    """清空进程内统计并卸下 handler（仅测试用）。

    ⚠️ 锁**不置 None**：别的线程可能正持有它。`setup` 幂等靠 `_handler is not None`
    判，不靠锁的初值。
    """
    global _handler, _log_dir, _write_failed
    with _samples_lock:
        _samples.clear()
    if _handler is not None:
        lg = logging.getLogger("limo.perf")
        try:
            lg.removeHandler(_handler)
            _handler.close()
        except Exception:  # noqa: BLE001
            pass
        _handler = None
    _log_dir = None
    _write_failed = False


__all__ = ["setup", "record", "timer", "snapshot", "reset"]
