"""任务队列 —— 认领、执行、恢复。**唯一一处**决定「下一个跑谁」。

## 分工

- `job_store`：表结构与读写（建、查、认领、收尾、恢复）。
- 本模块：谁先跑、跑多久算超时、失败写什么、取消怎么生效。
- `job_handlers`：具体每种 `kind` 干什么。

## 三条设计决定

1. **单消费者串行执行。** 这些任务都是重操作（pip、LLM 批量调用），并发的收益是
   让它们互相抢资源，故障面却翻倍。串行也让「取消全部」有确定的语义。
   将来真要并发，改的是 `loop` 里的一处：认领出 N 条、用信号量限流。
2. **同步 handler 跑在线程池**（`asyncio.to_thread`）。`install_plugin_deps` 里是
   `subprocess.run`，直接调会把整个事件循环卡几分钟 —— 期间连 `/health` 都不响应。
3. **取消是协作式的**（`JobContext.check_cancel`）。已经卡在 `subprocess.run` 里的
   同步调用杀不掉，所以取消的语义是「当前这一步结束后收尾」而不是「立刻中止」。
   这个差别写在返回给前端的 `message` 里，不让用户以为按钮没生效。
"""

from __future__ import annotations

import asyncio
import inspect
import threading
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from app.core import logging as log
from app.services.job_store import (
    CANCELLED,
    DONE,
    FAILED,
    Job,
    JobStore,
)

#: 空闲轮询间隔（秒）。主触发是 `submit` 里的 `wake()`（毫秒级）；这个值只兜
#: 「唤醒丢失」与「外部直接写了 jobs.db」两种情形，多长都不影响新任务的上手延迟。
DEFAULT_POLL_INTERVAL = 2.0

#: 终态任务保留条数。见 `JobStore.prune`。
KEEP_TERMINAL = 200


class JobCancelled(Exception):
    """handler 在检查点发现「有人要求取消」时抛这个。由 `_run` 收尾成 `cancelled`。"""


class JobFailed(Exception):
    """handler 想给用户一句可读的失败原因时抛这个（而不是裸 Exception）。

    裸异常的 `str()` 常是一串内部细节；这里允许 handler 把「用户该看到什么」
    和「日志里该记什么」分开。
    """

    def __init__(self, message: str, *, detail: str | None = None):
        super().__init__(message)
        self.detail = detail


@dataclass
class JobKind:
    handler: Callable[[Job, "JobContext"], Any | Awaitable[Any]]
    label: str
    #: True = 进程重启时把 `running` 改回 `queued` 重跑；False = 标失败。
    #: 判据是「重跑会不会再花钱 / 能不能容忍半程副作用」，不是「重跑容不容易」。
    retry_on_restart: bool = False
    #: 单次执行的上限（秒）。`None` = 不限。⚠️ 只对 async handler 真正可中断；
    #: 同步 handler 超时后任务会被判失败，但它那个线程还在跑，没法杀。
    timeout: float | None = None


class JobContext:
    """handler 与队列之间的那一个接口。

    刻意只暴露三件事：报进度、查取消、读 payload。handler 不能直接碰
    `JobStore` —— 否则它就能改状态 / 改别人的任务，而「谁写了这个字段」
    会变成需要读遍所有 handler 才能回答的问题。
    """

    def __init__(self, store: JobStore, job: Job, cancel_event: threading.Event):
        self._store = store
        self.job = job
        self._cancel = cancel_event

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def report(self, progress: float | None = None, message: str | None = None) -> None:
        """写进度（0–1）与一句话说明。两者都可省略。"""
        self._store.report(self.job.id, progress=progress, message=message)

    def check_cancel(self) -> None:
        """检查点。发现取消请求就抛 `JobCancelled`。"""
        if self._cancel.is_set():
            raise JobCancelled()


class JobQueue:
    """常驻一个 worker 协程的任务队列。"""

    def __init__(self, store: JobStore, *, poll_interval: float = DEFAULT_POLL_INTERVAL):
        self._store = store
        self._kinds: dict[str, JobKind] = {}
        self._poll = float(poll_interval)
        self._stop: asyncio.Event | None = None
        self._wake: asyncio.Event | None = None
        self._task: asyncio.Task | None = None
        #: 当前正在跑的那条的 (id, 取消事件)。取消接口据此置事件；没有它，
        #: `request_cancel` 只能改数据库里的旗标，而 handler 在下一个检查点之前
        #: 读不到 —— 那正是「点了取消，看起来没反应」的来源。
        self._active: tuple[str, threading.Event] | None = None

    @property
    def store(self) -> JobStore:
        """读口（路由层要 `list` / `get` / `dismiss`）。写入仍只走本类的方法 ——
        `_store` 不直接暴露，是为了让「状态谁改的」只有一个答案。"""
        return self._store

    # ── 注册 ──

    def register(self, kind: str, handler: Callable[..., Any], *, label: str,
                 retry_on_restart: bool = False, timeout: float | None = None) -> None:
        if kind in self._kinds:
            raise ValueError(f"任务类型重复注册：{kind}")
        self._kinds[kind] = JobKind(
            handler=handler, label=label,
            retry_on_restart=retry_on_restart, timeout=timeout,
        )

    @property
    def kinds(self) -> dict[str, str]:
        """`{kind: label}`，给前端 / capabilities 用，避免前端再抄一份中文名。"""
        return {k: v.label for k, v in self._kinds.items()}

    # ── 提交 ──

    def submit(self, kind: str, payload: dict[str, Any] | None = None,
               idempotency_key: str | None = None) -> tuple[Job, bool]:
        """建一条任务并叫醒 worker。返回 `(job, created)`。

        `created=False` = 幂等键命中了已有那条（`job_store.create` 的语义）。
        """
        if kind not in self._kinds:
            raise ValueError(f"未知的任务类型：{kind}")
        job, created = self._store.create(kind, payload, idempotency_key)
        if created:
            self.wake()
            log.info("任务已入队", kind=kind, job=job.id[:8])
        return job, created

    def wake(self) -> None:
        if self._wake is not None:
            self._wake.set()

    # ── 生命周期 ──

    async def start(self) -> dict[str, int]:
        """恢复上次的遗留 + 起后台循环。幂等。返回 `recover()` 的统计。"""
        if self._task is not None and not self._task.done():
            return {"requeued": 0, "failed": 0}
        retryable = {k for k, v in self._kinds.items() if v.retry_on_restart}
        recovered = self._store.recover(retryable)
        try:
            removed = self._store.prune(KEEP_TERMINAL)
            if removed:
                log.info("清理过期任务记录", removed=removed)
        except Exception as e:  # noqa: BLE001 —— 清理失败不该让队列起不来
            log.warning("清理任务记录失败（继续）", err=f"{type(e).__name__}: {e}"[:200])
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._task = asyncio.create_task(self.loop(), name="limo-job-queue")
        log.info("任务队列已启动", requeued=recovered["requeued"], failed=recovered["failed"])
        return recovered

    async def stop(self) -> None:
        stop, task = self._stop, self._task
        self._stop = None
        self._task = None
        self._wake = None
        if stop is not None:
            stop.set()
        if task is not None and not task.done():
            # `cancel()` 兜住「正卡在一次同步 handler 的线程等待 / 一次 LLM 往返」：
            # 只 set 的话，关闭窗口要等当前任务跑完。线程本身仍会跑完 —— 这是
            # Python 的线程无法被外部杀掉的已知代价，注释在 `_run` 里。
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:  # noqa: BLE001
                log.warning("任务队列退出异常", err=f"{type(e).__name__}: {e}"[:200])

    async def _sleep_until(self, stop: asyncio.Event, delay: float) -> None:
        wake = self._wake
        if wake is None:
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            return
        tasks = [asyncio.ensure_future(stop.wait()), asyncio.ensure_future(wake.wait())]
        try:
            await asyncio.wait(tasks, timeout=delay, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            wake.clear()

    async def loop(self) -> None:
        """取一条 → 跑 → 再取。没有可取的条就睡到「被叫醒 / 要求停止」。"""
        stop = self._stop
        assert stop is not None
        while not stop.is_set():
            try:
                job = self._store.claim_next()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 —— 库坏了不该让队列死掉
                log.warning("任务认领失败", err=f"{type(e).__name__}: {e}"[:200])
                job = None
            if job is None:
                await self._sleep_until(stop, self._poll)
                continue
            try:
                await self._run(job)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 —— `_run` 自己会收尾，这只是兜底
                log.warning("任务执行异常", job=job.id[:8], err=f"{type(e).__name__}: {e}"[:200])

    async def _run(self, job: Job) -> None:
        spec = self._kinds.get(job.kind)
        if spec is None:
            # 注册表与库里的 kind 对不上（降级安装、改了 kind 名）时，任务必须
            # 落成失败而不是永远 `running` —— 否则前端每次刷新都看到一条「在跑」。
            self._store.finish(job.id, FAILED, error=f"未注册的任务类型：{job.kind}")
            return

        cancel_event = threading.Event()
        if job.cancel_requested:
            cancel_event.set()
        self._active = (job.id, cancel_event)
        ctx = JobContext(self._store, job, cancel_event)
        log.info("任务开始", kind=job.kind, job=job.id[:8], attempt=job.attempts)

        try:
            if inspect.iscoroutinefunction(spec.handler):
                coro = spec.handler(job, ctx)
                result = await (asyncio.wait_for(coro, spec.timeout) if spec.timeout else coro)
            else:
                # ⚠️ `to_thread` 里的线程**不会**被 cancel / timeout 杀掉。超时在这里
                # 只让队列往下走并判定失败；那个线程会继续跑到自己的结束点。
                # 对 pip / LLM 这类「跑完总比悬挂好」的操作，这是可接受的取舍。
                fut = asyncio.to_thread(spec.handler, job, ctx)
                result = await (asyncio.wait_for(fut, spec.timeout) if spec.timeout else fut)

            # handler 返回之后再查一次：同步 handler 内部没有检查点，取消只在
            # 「这一步结束之后」生效 —— 所以这里必须把「其实已被取消」写进记录，
            # 不然用户点了取消、看到的却是「已完成」。
            if cancel_event.is_set():
                self._store.finish(job.id, CANCELLED, result=result, error="已取消")
                log.info("任务已取消", kind=job.kind, job=job.id[:8])
            else:
                self._store.finish(job.id, DONE, result=result)
                log.info("任务完成", kind=job.kind, job=job.id[:8])
        except JobCancelled:
            self._store.finish(job.id, CANCELLED, error="已取消")
            log.info("任务已取消", kind=job.kind, job=job.id[:8])
        except asyncio.TimeoutError:
            self._store.finish(job.id, FAILED, error=f"执行超时（{spec.timeout:.0f} 秒）")
            log.warning("任务超时", kind=job.kind, job=job.id[:8])
        except JobFailed as e:
            self._store.finish(job.id, FAILED, error=str(e))
            log.warning("任务失败", kind=job.kind, job=job.id[:8],
                        err=str(e)[:200], detail=(e.detail or "")[:300] or None)
        except asyncio.CancelledError:
            # 进程在停机 / worker 被 cancel。任务留在 `running`，下次启动由
            # `JobStore.recover` 按 kind 决定重跑还是标失败 —— 这里不写终态，
            # 否则「重启后还能接着装」会变成不可能。
            raise
        except Exception as e:
            self._store.finish(job.id, FAILED, error=f"{type(e).__name__}: {e}"[:500])
            log.warning("任务失败", kind=job.kind, job=job.id[:8],
                        err=f"{type(e).__name__}: {e}"[:200])
        finally:
            self._active = None

    # ── 取消 ──

    def request_cancel(self, job_id: str) -> Job | None:
        job = self._store.request_cancel(job_id)
        if job is None:
            return None
        active = self._active
        if active is not None and active[0] == job_id:
            active[1].set()
            # 给用户一句实话：同步步骤杀不掉，「正在收尾」不等于「已经停了」。
            self._store.report(job_id, message="正在取消，当前步骤结束后停止")
        return job
