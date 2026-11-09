"""任务队列（`services/job_store.py` + `services/job_queue.py`）单测。

覆盖的是三条最容易做错的约定，各配一组对照：

1. **幂等键只挡「同时跑两遍」**。上一条还在 `queued` / `running` 时再提交同键 →
   拿回同一条；上一条已结束时同键 → 建一条新的（否则失败一次就永远点不动）。
2. **认领是原子的，且恢复语义按 kind 分**。重跑无副作用的回 `queued`，
   要花钱的标 `failed` 并写明「进程中断」。
3. **取消分两种**：`queued` 立即落成 `cancelled`；`running` 置旗标，handler 在
   检查点抛出，收尾成 `cancelled`（而不是「已完成」）。
"""

import asyncio
import time

import pytest

from app.services.job_queue import JobCancelled, JobFailed, JobQueue
from app.services.job_store import (
    CANCELLED,
    DONE,
    FAILED,
    QUEUED,
    RUNNING,
    JobStore,
)


@pytest.fixture()
def store(tmp_path):
    return JobStore(tmp_path)


# ── 存储层 ──

def test_create_get_list(store):
    job, created = store.create("demo", {"x": 1}, None)
    assert created and job.status == QUEUED
    got = store.get(job.id)
    assert got is not None and got.payload == {"x": 1}
    assert [j.id for j in store.list()] == [job.id]


def test_幂等键只在进行中时占位(store):
    a, created_a = store.create("demo", None, "k1")
    b, created_b = store.create("demo", None, "k1")
    assert created_a and not created_b
    assert b.id == a.id

    # 结束后键释放：同键建出新的一条（重试是用户的权利）
    store.finish(a.id, DONE, result={"ok": True})
    c, created_c = store.create("demo", None, "k1")
    assert created_c and c.id != a.id


def test_认领原子且attempts自增(store):
    a, _ = store.create("demo", None, None)
    b, _ = store.create("demo", None, None)
    first = store.claim_next()
    second = store.claim_next()
    assert {first.id, second.id} == {a.id, b.id}
    assert store.claim_next() is None
    assert first.attempts == 1 and second.attempts == 1
    assert first.status == second.status == RUNNING


def test_恢复语义按kind分(store):
    retryable, _ = store.create("install", None, None)
    costly, _ = store.create("synth", None, None)
    store.claim_next()
    store.claim_next()

    out = store.recover({"install"})
    assert out == {"requeued": 1, "failed": 1}
    assert store.get(retryable.id).status == QUEUED
    done = store.get(costly.id)
    assert done.status == FAILED and "进程中断" in done.error


def test_取消排队中的任务立即生效(store):
    job, _ = store.create("demo", None, None)
    out = store.request_cancel(job.id)
    assert out.status == CANCELLED
    # 已经被取消的不该再被认领
    assert store.claim_next() is None


def test_dismiss后不再出现在默认列表(store):
    job, _ = store.create("demo", None, None)
    store.claim_next()
    store.finish(job.id, FAILED, error="boom")
    assert [j.id for j in store.list()] == [job.id]
    store.dismiss(job.id)
    assert store.list() == []
    # 显式包含时仍读得到（诊断用）
    assert [j.id for j in store.list(include_dismissed=True)] == [job.id]


def test_prune只留最近若干条终态(store):
    ids = []
    for i in range(5):
        j, _ = store.create("demo", {"i": i}, None)
        store.claim_next()
        store.finish(j.id, DONE)
        ids.append(j.id)
        time.sleep(0.002)  # created_at 是秒级浮点，拉开顺序
    assert store.prune(keep_terminal=2) == 3
    left = {j.id for j in store.list(include_dismissed=True)}
    assert left == set(ids[-2:])


# ── 队列层 ──

async def _wait_terminal(store, job_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = store.get(job_id)
        if job.status not in (QUEUED, RUNNING):
            return job
        await asyncio.sleep(0.01)
    raise AssertionError(f"任务 {job_id} 在 {timeout}s 内没有结束")


def test_同步handler跑完写结果与进度(store):
    async def main():
        q = JobQueue(store, poll_interval=0.02)
        seen = {}

        def handler(job, ctx):
            ctx.report(progress=0.5, message="走到一半")
            seen["mid"] = store.get(job.id).progress
            return {"value": job.payload["n"] * 2}

        q.register("double", handler, label="翻倍")
        await q.start()
        try:
            job, created = q.submit("double", {"n": 21})
            assert created
            done = await _wait_terminal(store, job.id)
        finally:
            await q.stop()
        assert done.status == DONE
        assert done.result == {"value": 42}
        assert seen["mid"] == pytest.approx(0.5)

    asyncio.run(main())


def test_未注册的kind直接拒绝提交(store):
    q = JobQueue(store, poll_interval=0.02)
    with pytest.raises(ValueError):
        q.submit("nope", {})
    assert store.list() == []


def test_JobFailed的message就是用户看到的那句(store):
    async def main():
        q = JobQueue(store, poll_interval=0.02)

        def handler(job, ctx):
            raise JobFailed("网络不通，稍后再试", detail="connect timeout")

        q.register("boom", handler, label="会失败")
        await q.start()
        try:
            job, _ = q.submit("boom", {})
            done = await _wait_terminal(store, job.id)
        finally:
            await q.stop()
        assert done.status == FAILED
        assert done.error == "网络不通，稍后再试"

    asyncio.run(main())


def test_裸异常落成failed而不是让循环死掉(store):
    async def main():
        q = JobQueue(store, poll_interval=0.02)

        def handler(job, ctx):
            raise RuntimeError("内部炸了")

        q.register("crash", handler, label="崩溃")
        q.register("ok", lambda job, ctx: "fine", label="正常")
        await q.start()
        try:
            bad, _ = q.submit("crash", {})
            bad_done = await _wait_terminal(store, bad.id)
            # 上一条崩了之后队列仍然在工作 —— 这是「单轮异常不能杀死常驻任务」
            good, _ = q.submit("ok", {})
            good_done = await _wait_terminal(store, good.id)
        finally:
            await q.stop()
        assert bad_done.status == FAILED and "内部炸了" in bad_done.error
        assert good_done.status == DONE and good_done.result == "fine"

    asyncio.run(main())


def test_运行中取消在检查点收尾成cancelled(store):
    async def main():
        q = JobQueue(store, poll_interval=0.02)

        async def handler(job, ctx):
            # 模拟一个可中断的长操作：每 10ms 检查一次取消
            for _ in range(500):
                await asyncio.sleep(0.01)
                ctx.check_cancel()
            return "不该跑到这里"

        q.register("slow", handler, label="长操作")
        await q.start()
        try:
            job, _ = q.submit("slow", {})
            deadline = time.monotonic() + 3
            while store.get(job.id).status != RUNNING and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            q.request_cancel(job.id)
            done = await _wait_terminal(store, job.id)
        finally:
            await q.stop()
        assert done.status == CANCELLED

    asyncio.run(main())


def test_同步handler返回后若已请求取消则记为cancelled(store):
    """同步 handler 内部没有检查点 —— 取消要等到它返回，且**必须**记成 cancelled。"""
    async def main():
        q = JobQueue(store, poll_interval=0.02)
        started = asyncio.Event()

        def handler(job, ctx):
            # 用 asyncio 的线程安全调用唤醒主协程，再等一小会模拟长任务
            loop.call_soon_threadsafe(started.set)
            time.sleep(0.2)
            return "done"

        loop = asyncio.get_running_loop()
        q.register("blocking", handler, label="同步长操作")
        await q.start()
        try:
            job, _ = q.submit("blocking", {})
            await started.wait()
            q.request_cancel(job.id)
            done = await _wait_terminal(store, job.id)
        finally:
            await q.stop()
        assert done.status == CANCELLED

    asyncio.run(main())


def test_重启恢复后可重跑的任务真的会被再执行一次(tmp_path):
    async def main():
        first = JobStore(tmp_path)
        job, _ = first.create("install", {"name": "x"}, None)
        first.claim_next()  # 模拟「跑到一半进程被杀」

        second = JobStore(tmp_path)
        q = JobQueue(second, poll_interval=0.02)
        ran = []

        def handler(j, ctx):
            ran.append(j.id)
            return "ok"

        q.register("install", handler, label="安装", retry_on_restart=True)
        await q.start()
        try:
            done = await _wait_terminal(second, job.id)
        finally:
            await q.stop()
        assert ran == [job.id]
        assert done.status == DONE

    asyncio.run(main())


def test_JobCancelled不是错误(store):
    """handler 主动抛 `JobCancelled` 的语义与检查点一致，都是「用户不要了」。"""
    async def main():
        q = JobQueue(store, poll_interval=0.02)

        def handler(job, ctx):
            raise JobCancelled

        q.register("cancel", handler, label="取消")
        await q.start()
        try:
            job, _ = q.submit("cancel", {})
            done = await _wait_terminal(store, job.id)
        finally:
            await q.stop()
        assert done.status == CANCELLED

    asyncio.run(main())
