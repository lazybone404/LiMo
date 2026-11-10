"""验证 epub 一键解读管道 — 逐章解读 + 归并总结 + 中断续跑 + 取消 + 互斥

运行：cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_epub_pipeline.py
注入点：替换 epub_pipeline._llm_factory 返回 FakeLLM（不 monkeypatch 类方法）。

⚠️ 判据纪律（P1-01，2026-10-06）：本文件原先只有
`if __name__ == "__main__": asyncio.run(main())` 一个入口 —— 40 条 assert 全在
`scenario_*` 里，**只有手工跑脚本时可达**。pytest 收集 0 项、exit 0，
于是 `check-all` 的 backend 层从来没有执行过这一条管道。现在每个场景各自是一个
pytest 用例（脚本入口已删除，避免「两个入口各自演进」）。

⚠️ **2026-11-10 起走真实路径**：解读跑在任务队列里，所以这里也经
`register_builtin_handlers` + `JobQueue.submit` 驱动，而不是直接调管道函数。
原先那套 `start_epub_task` / `cancel_epub_task` 已经删除 —— 它们维护的
`_tasks` 内存表正是「切走页面就看不见」的来源。判据（阶段推进、失败跳过、
续跑、取消保留进度）一条没变，只是「在不在跑」现在由 `jobs.db` 回答。
"""

import asyncio
import contextlib
import json
import os
import re
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest

from app.core.types import RawTranscript, SourceType
from app.llm.errors import CancelledError
from app.services import epub_pipeline
from app.services.errors import EpubError
from app.services.job_handlers import register_builtin_handlers
from app.services.job_queue import JobQueue
from app.services.job_store import CANCELLED, DONE, FAILED, Job, JobStore
from app.storage.store import Store

_ORIG_FACTORY = epub_pipeline._llm_factory

CHAPTER_OK = "## 本章概述\n\n这是本章的解读内容要点。\n\n- 要点一\n- 要点二"
SUMMARY_OK = "# 《测试书》导读\n\n## 主线脉络\n\n全书讲了测试内容。"


class FakeLLM:
    """可编程 FakeLLM：
    - fail_times: {章节位置: 永久/几次失败} —— 章节调用先失败 n 次后成功
    - permanent_fail: 章节位置集合 —— 永远失败
    - delay: 每次 call 前 sleep（模拟慢调用，供取消/互斥测试）
    """
    def __init__(self, chapter_text=CHAPTER_OK, summary_text=SUMMARY_OK,
                 fail_times=None, permanent_fail=None, delay=0.0, hang=False):
        self.chapter_text = chapter_text
        self.summary_text = summary_text
        self.fail_times = fail_times or {}      # {idx: 失败次数}（idx = 位置）
        self.permanent_fail = set(permanent_fail or ())
        self.delay = delay
        self.hang = hang
        self.failed = {}          # {idx: 已失败次数}
        self.chapter_calls = []   # 记录章节 prompt（含「请对该章节进行」）
        self.summary_calls = []   # 记录阶段 2 调用（子总结 + 归并）

    async def call(self, prompt, **kwargs):
        if self.hang:
            await asyncio.sleep(30)
        ce = kwargs.get("cancel_event")
        if ce is not None and ce.is_set():
            raise CancelledError("cancelled")
        if self.delay:
            await asyncio.sleep(self.delay)
        if "请对该章节进行" in prompt:
            m = re.search(r"第 (\d+) 章", prompt)
            idx = int(m.group(1)) - 1
            if idx in self.permanent_fail:
                raise RuntimeError("mock 章节永久失败")
            n_fail = self.fail_times.get(idx, 0)
            cur = self.failed.get(idx, 0)
            if cur < n_fail:
                self.failed[idx] = cur + 1
                raise RuntimeError("mock 章节失败")
            self.chapter_calls.append(prompt)
            return self.chapter_text
        self.summary_calls.append(prompt)
        return self.summary_text


def _make_store(n_chapters=3, title="测试书", doc_id="tr_epubtest") -> Store:
    d = tempfile.mkdtemp()
    store = Store(d)
    chapters = [{
        "index": i + 1, "title": f"第{i + 1}章标题",
        "sections": [{"title": f"第{i + 1}节", "text": f"第{i + 1}章正文内容……",
                      "skeleton": f"第{i + 1}章骨架重点"}],
    } for i in range(n_chapters)]
    raw = json.dumps({"chapters": chapters, "metadata": {}, "total_sections": n_chapters})
    tx = RawTranscript(id=doc_id, title=title, source_type=SourceType.EPUB,
                       engine="epub-container", raw_text=raw,
                       content_hash="h" + doc_id + str(int(time.time() * 1000)))
    assert store.save_transcript(tx)
    return store


@contextlib.asynccontextmanager
async def _queue(store: Store, fake: FakeLLM):
    """一个跑在临时目录里的真队列（jobs.db 与 store 同目录，各自独立）。"""
    epub_pipeline._llm_factory = lambda config, mid: fake
    job_store = JobStore(store.root)
    queue = JobQueue(job_store, poll_interval=0.02)
    register_builtin_handlers(queue, store=store, config_getter=lambda: {}, guard=None)
    await queue.start()
    try:
        yield queue, job_store
    finally:
        await queue.stop()


def _submit(queue: JobQueue, doc_id="tr_epubtest", **payload) -> tuple[Job, bool]:
    return queue.submit(
        "epub.interpret",
        {"doc_id": doc_id, "model_id": 1, **payload},
        idempotency_key=f"epub.interpret:{doc_id}",
    )


async def _wait_terminal(job_store: JobStore, job_id: str, timeout=8.0) -> Job:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = job_store.get(job_id)
        if job is not None and job.status not in ("queued", "running"):
            return job
        await asyncio.sleep(0.05)
    return job_store.get(job_id)


def _read_md(store, doc_id, mode):
    """读落盘的 markdown（最新版）"""
    mds = store.get_markdowns_by_transcript(doc_id, mode=mode)
    return mds[0]["content"] if mds else None


# ═══ 场景 1：逐章成功全流程 ═══
async def scenario_success():
    store = _make_store(3)
    fake = FakeLLM()
    async with _queue(store, fake) as (queue, js):
        job, _ = _submit(queue)
        done = await _wait_terminal(js, job.id)
    assert done.status == DONE, f"status={done.status} error={done.error}"
    assert done.result["chapters_done"] == 3 and done.result["chapters_failed"] == 0
    assert len(fake.chapter_calls) == 3, f"章节调用 {len(fake.chapter_calls)}"
    polished = _read_md(store, "tr_epubtest", "polished")
    notes = _read_md(store, "tr_epubtest", "notes")
    assert polished and "解读内容" in polished
    assert notes and "主线脉络" in notes


# ═══ 场景 2：失败章跳过 + 产物仍落盘 + polished 含失败标注 ═══
async def scenario_fail_skip():
    store = _make_store(3)
    fake = FakeLLM(permanent_fail={1})  # 第 2 章（位置 1）永久失败
    async with _queue(store, fake) as (queue, js):
        job, _ = _submit(queue)
        done = await _wait_terminal(js, job.id)
    assert done.status == DONE, f"status={done.status} error={done.error}"
    assert done.result["chapters_failed"] == 1 and done.result["chapters_done"] == 2
    polished = _read_md(store, "tr_epubtest", "polished")
    assert "解读失败" in polished, "polished 应含失败章标注"
    assert polished.count("解读失败") == 1
    notes = _read_md(store, "tr_epubtest", "notes")
    assert notes and "主线脉络" in notes, "阶段 2 用成功章归并，不受失败影响"


# ═══ 场景 3：失败重试后成功 ═══
async def scenario_retry_recover():
    store = _make_store(1)
    fake = FakeLLM(fail_times={0: 2})  # 第 1 章前 2 次失败，第 3 次成功
    async with _queue(store, fake) as (queue, js):
        job, _ = _submit(queue)
        done = await _wait_terminal(js, job.id)
    assert done.status == DONE
    assert done.result["chapters_done"] == 1 and done.result["chapters_failed"] == 0


# ═══ 场景 4：全部章失败极端情况 ═══
async def scenario_all_fail():
    store = _make_store(2)
    fake = FakeLLM(permanent_fail={0, 1})
    async with _queue(store, fake) as (queue, js):
        job, _ = _submit(queue)
        done = await _wait_terminal(js, job.id)
    assert done.status == DONE, "全部章失败也必须落盘 done"
    assert done.result["chapters_failed"] == 2
    polished = _read_md(store, "tr_epubtest", "polished")
    notes = _read_md(store, "tr_epubtest", "notes")
    assert polished and polished.count("解读失败") == 2
    assert notes and "生成失败" in notes, "notes 应含明确失败说明（不静默）"


# ═══ 场景 5：阶段 2 超限分组 map-reduce ═══
async def scenario_summary_split():
    store = _make_store(3)
    big = "长" * 10000  # 3 × 10k → source ~30k > 24000 触发分组
    fake = FakeLLM(chapter_text="## 本章概述\n\n" + big)
    async with _queue(store, fake) as (queue, js):
        job, _ = _submit(queue)
        done = await _wait_terminal(js, job.id)
    assert done.status == DONE
    # 阶段 2：子总结（2 组）+ 归并（1 次）= 3 次 summary 调用
    assert len(fake.summary_calls) == 3, f"summary 调用 {len(fake.summary_calls)}"
    assert any("子总结" in p for p in fake.summary_calls)
    assert any("若干子总结" in p for p in fake.summary_calls)


# ═══ 场景 6：续跑跳过 done + 重试 failed + 重新归并 ═══
async def scenario_resume():
    store = _make_store(3)
    # 预置中断现场：0、1 已 done（片段落盘），2 failed
    store.save_epub_chapter("tr_epubtest", 0, "第1章标题", CHAPTER_OK)
    store.save_epub_chapter("tr_epubtest", 1, "第2章标题", CHAPTER_OK)
    store.save_epub_task("tr_epubtest", {
        "stage": "chapters", "chapters_done": [0, 1], "chapters_failed": [2],
        "total_chapters": 3, "model_id": 1, "deep": False, "current_title": "",
    })
    fake = FakeLLM()
    async with _queue(store, fake) as (queue, js):
        job, _ = _submit(queue)
        done = await _wait_terminal(js, job.id)
    assert done.status == DONE
    # 续跑只重试 failed 第 3 章（1 次章节调用），跳过前两章
    assert len(fake.chapter_calls) == 1, f"续跑章节调用 {len(fake.chapter_calls)}"
    assert done.result["chapters_done"] == 3 and done.result["chapters_failed"] == 0
    polished = _read_md(store, "tr_epubtest", "polished")
    assert "第1章" in polished and "解读失败" not in polished


# ═══ 场景 7：取消保留进度 ═══
async def scenario_cancel():
    store = _make_store(5)
    fake = FakeLLM(delay=0.3)  # 每章 0.3s，保证取消来得及
    async with _queue(store, fake) as (queue, js):
        job, _ = _submit(queue)
        await asyncio.sleep(0.45)  # 第 1 章进行中
        assert queue.request_cancel(job.id) is not None
        done = await _wait_terminal(js, job.id)
    assert done.status == CANCELLED, f"取消应落成 cancelled，实际 {done.status}"
    state = store.load_epub_task("tr_epubtest")
    assert state["stage"] != "done", "取消后不得进入 done"
    assert len(state["chapters_done"]) < 5, "取消应保留已完成的章进度"

    # 取消后再次启动 → 续跑（幂等键已随终态释放）
    fake2 = FakeLLM()
    async with _queue(store, fake2) as (queue, js):
        job, created = _submit(queue)
        assert created, "上一个任务已结束，同键应建出新的一条"
        done2 = await _wait_terminal(js, job.id)
    assert done2.status == DONE


# ═══ 场景 8：互斥（同一文档进行中重复提交 → 同一条任务） ═══
async def scenario_busy():
    store = _make_store(3)
    fake = FakeLLM(hang=True)  # 卡住不返回
    async with _queue(store, fake) as (queue, js):
        job, created = _submit(queue)
        assert created
        again, created2 = _submit(queue)
        # 幂等键命中的语义：不报错、不建第二条 —— 用户要的就是那一条
        assert not created2 and again.id == job.id
        queue.request_cancel(job.id)
        await _wait_terminal(js, job.id)


# ═══ 场景 9：不可解读的三种情况 + 失败原因穿过任务记录 ═══
def scenario_preflight_errors():
    store = _make_store(0, doc_id="tr_epubtest_err")  # 无章节
    tx = RawTranscript(id="tr_txt", title="纯文本", source_type=SourceType.TEXT,
                       engine="text", raw_text="hello", content_hash="hash_txt_1")
    assert store.save_transcript(tx)

    for doc_id, code in (("tr_txt", "EPUB_NOT_EPUB"),
                         ("tr_epubtest_err", "EPUB_NO_CHAPTERS"),
                         ("不存在", "EPUB_TX_NOT_FOUND")):
        with pytest.raises(EpubError) as e:
            epub_pipeline.preflight(store, doc_id)
        assert e.value.code == code, f"{doc_id} 期望 {code}，实际 {e.value.code}"


async def scenario_error_job():
    """错误码穿过 `jobs.db` 一轮仍然在 —— 前端靠它分类，不靠匹配文案。"""
    store = _make_store(0, doc_id="tr_epubtest_err")
    tx = RawTranscript(id="tr_txt", title="纯文本", source_type=SourceType.TEXT,
                       engine="text", raw_text="hello", content_hash="hash_txt_2")
    assert store.save_transcript(tx)
    async with _queue(store, FakeLLM()) as (queue, js):
        job, _ = _submit(queue, doc_id="tr_txt")
        done = await _wait_terminal(js, job.id)
    assert done.status == FAILED
    assert done.result == {"code": "EPUB_NOT_EPUB"}
    assert done.error == "该文档不是电子书，无法进行章节解读"


# ═══ pytest 入口 ═══
# 每个 scenario 一个用例。`asyncio.run` 而非 anyio/pytest-asyncio：各场景彼此独立
# （各自建 store、各用独立 doc_id），不需要共享事件循环，也就不必引入新插件依赖。


@pytest.fixture(scope="module", autouse=True)
def _restore_llm_factory():
    """场景替换了 `epub_pipeline._llm_factory`，模块跑完恢复原值。"""
    yield
    epub_pipeline._llm_factory = _ORIG_FACTORY


def test_epub_success():
    """逐章解读 → 全部成功后触发归并，notes=总结 / polished=逐章。"""
    asyncio.run(scenario_success())


def test_epub_fail_skip():
    """单章永久失败 → 跳过并在 polished 按序插失败标注，其余照常。"""
    asyncio.run(scenario_fail_skip())


def test_epub_retry_recover():
    """单章先失败 n 次后成功 → 重试机制生效。"""
    asyncio.run(scenario_retry_recover())


def test_epub_all_fail():
    """全部章节失败 → 仍落盘失败产物，不静默丢文档。"""
    asyncio.run(scenario_all_fail())


def test_epub_summary_split():
    """章节过多 → 阶段 2 先子总结再归并（分组切分路径）。"""
    asyncio.run(scenario_summary_split())


def test_epub_resume():
    """中断后重跑 → 只补未完成的章，不重复解读已完成的。"""
    asyncio.run(scenario_resume())


def test_epub_cancel():
    """取消：任务落成 cancelled、产物保留，可继续上次解读。"""
    asyncio.run(scenario_cancel())


def test_epub_busy():
    """互斥：同一文档进行中重复提交 → 拿回同一条任务，不报错也不重跑。"""
    asyncio.run(scenario_busy())


def test_epub_preflight_errors():
    """非 epub / 无章节 / 文档不存在 三个错误分支。"""
    scenario_preflight_errors()


def test_epub_error_job():
    """预检错误穿过任务记录 → failed + 结构化 code。"""
    asyncio.run(scenario_error_job())
