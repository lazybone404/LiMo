"""验证 epub 一键解读管道 — 逐章解读 + 归并总结 + 中断续跑 + 取消 + 互斥

运行：cd backend && python tests/test_epub_pipeline.py
注入点：替换 epub_pipeline._llm_factory 返回 FakeLLM（不 monkeypatch 类方法）。
"""

import asyncio
import json
import os
import re
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.storage.store import Store
from app.core.types import RawTranscript, SourceType
from app.llm.errors import CancelledError
from app.services import epub_pipeline
from app.services.errors import EpubError

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
                       engine="ebooklib", raw_text=raw,
                       content_hash="h" + doc_id + str(int(time.time() * 1000)))
    assert store.save_transcript(tx)
    return store


def _install_fake(fake: FakeLLM):
    epub_pipeline._llm_factory = lambda config, mid: fake


async def _wait_done(store, doc_id, timeout=8.0) -> dict:
    """等待任务结束（stage=done 或 running=False）。返回最终状态。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = epub_pipeline.get_epub_task(store, doc_id)
        if not st["running"] and st["stage"] == "done":
            return st
        if not st["running"] and st["stage"] is not None:
            return st  # 取消/中断但未 done
        await asyncio.sleep(0.05)
    return epub_pipeline.get_epub_task(store, doc_id)


def _read_md(store, doc_id, mode):
    """读落盘的 markdown（最新版）"""
    mds = store.get_markdowns_by_transcript(doc_id, mode=mode)
    return mds[0]["content"] if mds else None


# ═══ 场景 1：逐章成功全流程 ═══
async def scenario_success():
    store = _make_store(3)
    fake = FakeLLM()
    _install_fake(fake)
    epub_pipeline.start_epub_task(store, {}, "tr_epubtest", model_id=1, deep=False)
    st = await _wait_done(store, "tr_epubtest")
    assert st["stage"] == "done", f"stage={st['stage']}"
    assert len(st["chapters_done"]) == 3 and len(st["chapters_failed"]) == 0
    assert len(fake.chapter_calls) == 3, f"章节调用 {len(fake.chapter_calls)}"
    polished = _read_md(store, "tr_epubtest", "polished")
    notes = _read_md(store, "tr_epubtest", "notes")
    assert polished and "解读内容" in polished
    assert notes and "主线脉络" in notes
    print("PASS: 逐章成功全流程（阶段1→2→done，notes/polished 落盘）")


# ═══ 场景 2：失败章跳过 + 产物仍落盘 + polished 含失败标注 ═══
async def scenario_fail_skip():
    store = _make_store(3)
    fake = FakeLLM(permanent_fail={1})  # 第 2 章（位置 1）永久失败
    _install_fake(fake)
    epub_pipeline.start_epub_task(store, {}, "tr_epubtest", model_id=1)
    st = await _wait_done(store, "tr_epubtest")
    assert st["stage"] == "done", f"stage={st['stage']}"
    assert 1 in st["chapters_failed"] and len(st["chapters_done"]) == 2
    polished = _read_md(store, "tr_epubtest", "polished")
    assert "解读失败" in polished, "polished 应含失败章标注"
    assert polished.count("解读失败") == 1
    notes = _read_md(store, "tr_epubtest", "notes")
    assert notes and "主线脉络" in notes, "阶段 2 用成功章归并，不受失败影响"
    print("PASS: 失败章跳过 + 产物仍落盘 + polished 失败标注 + 阶段2正常归并")


# ═══ 场景 3：失败重试后成功 ═══
async def scenario_retry_recover():
    store = _make_store(1)
    fake = FakeLLM(fail_times={0: 2})  # 第 1 章前 2 次失败，第 3 次成功
    _install_fake(fake)
    epub_pipeline.start_epub_task(store, {}, "tr_epubtest", model_id=1)
    st = await _wait_done(store, "tr_epubtest")
    assert st["stage"] == "done"
    assert 0 in st["chapters_done"] and not st["chapters_failed"]
    print("PASS: 失败章重试后成功（CHAPTER_RETRY 内恢复）")


# ═══ 场景 4：全部章失败极端情况 ═══
async def scenario_all_fail():
    store = _make_store(2)
    fake = FakeLLM(permanent_fail={0, 1})
    _install_fake(fake)
    epub_pipeline.start_epub_task(store, {}, "tr_epubtest", model_id=1)
    st = await _wait_done(store, "tr_epubtest")
    assert st["stage"] == "done", "全部章失败也必须落盘 done"
    assert len(st["chapters_failed"]) == 2
    polished = _read_md(store, "tr_epubtest", "polished")
    notes = _read_md(store, "tr_epubtest", "notes")
    assert polished and polished.count("解读失败") == 2
    assert notes and "生成失败" in notes, "notes 应含明确失败说明（不静默）"
    print("PASS: 全部章失败极端情况（产物仍落盘 + 明确失败说明）")


# ═══ 场景 5：阶段 2 超限分组 map-reduce ═══
async def scenario_summary_split():
    store = _make_store(3)
    big = "长" * 10000  # 3 × 10k → source ~30k > 24000 触发分组
    fake = FakeLLM(chapter_text="## 本章概述\n\n" + big)
    _install_fake(fake)
    epub_pipeline.start_epub_task(store, {}, "tr_epubtest", model_id=1)
    st = await _wait_done(store, "tr_epubtest")
    assert st["stage"] == "done"
    # 阶段 2：子总结（2 组）+ 归并（1 次）= 3 次 summary 调用
    assert len(fake.summary_calls) == 3, f"summary 调用 {len(fake.summary_calls)}"
    assert any("子总结" in p for p in fake.summary_calls)
    assert any("若干子总结" in p for p in fake.summary_calls)
    print("PASS: 阶段2 超限分组 map-reduce（子总结+归并）")


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
    _install_fake(fake)
    epub_pipeline.start_epub_task(store, {}, "tr_epubtest", model_id=1)
    st = await _wait_done(store, "tr_epubtest")
    assert st["stage"] == "done"
    # 续跑只重试 failed 第 3 章（1 次章节调用），跳过前两章
    assert len(fake.chapter_calls) == 1, f"续跑章节调用 {len(fake.chapter_calls)}"
    assert len(st["chapters_done"]) == 3 and not st["chapters_failed"]
    polished = _read_md(store, "tr_epubtest", "polished")
    assert "第1章" in polished and "解读失败" not in polished
    print("PASS: 续跑跳过 done + 重试 failed + 重试成功后重新归并")


# ═══ 场景 7：取消保留进度 ═══
async def scenario_cancel():
    store = _make_store(5)
    fake = FakeLLM(delay=0.3)  # 每章 0.3s，保证取消来得及
    _install_fake(fake)
    epub_pipeline.start_epub_task(store, {}, "tr_epubtest", model_id=1)
    await asyncio.sleep(0.45)  # 第 1 章进行中
    assert epub_pipeline.cancel_epub_task("tr_epubtest"), "cancel 应命中内存任务"
    st = await _wait_done(store, "tr_epubtest")
    assert not st["running"]
    assert st["stage"] != "done", "取消后不得进入 done"
    assert st["stage"] in ("chapters", "summary")
    assert len(st["chapters_done"]) < 5, "取消应保留已完成的章进度"
    # 取消后再次启动 → 续跑（非互斥）
    fake2 = FakeLLM()
    _install_fake(fake2)
    epub_pipeline.start_epub_task(store, {}, "tr_epubtest", model_id=1)
    st2 = await _wait_done(store, "tr_epubtest")
    assert st2["stage"] == "done"
    print("PASS: 取消保留进度 + 取消后重新启动续跑")


# ═══ 场景 8：互斥（任务进行中重复启动被拒） ═══
async def scenario_busy():
    store = _make_store(3)
    fake = FakeLLM(hang=True)  # 卡住不返回
    _install_fake(fake)
    epub_pipeline.start_epub_task(store, {}, "tr_epubtest", model_id=1)
    try:
        epub_pipeline.start_epub_task(store, {}, "tr_epubtest", model_id=1)
        assert False, "应抛 EPUB_BUSY"
    except EpubError as e:
        assert e.code == "EPUB_BUSY"
    assert epub_pipeline.cancel_epub_task("tr_epubtest")
    print("PASS: 互斥（进行中重复启动被拒 + 可取消）")


# ═══ 场景 9：非 epub / 无章节 错误分支 ═══
def scenario_errors():
    store = _make_store(0, doc_id="tr_epubtest_err")  # 无章节（独立 doc_id，避开 _tasks 残留）
    # 非 epub
    tx = RawTranscript(id="tr_txt", title="纯文本", source_type=SourceType.TEXT,
                       engine="text", raw_text="hello", content_hash="hash_txt_1")
    assert store.save_transcript(tx)
    try:
        epub_pipeline.start_epub_task(store, {}, "tr_txt", model_id=1)
        assert False, "非 epub 应抛错"
    except EpubError as e:
        assert e.code == "EPUB_NOT_EPUB"
    try:
        epub_pipeline.start_epub_task(store, {}, "tr_epubtest_err", model_id=1)
        assert False, "无章节应抛错"
    except EpubError as e:
        assert e.code == "EPUB_NO_CHAPTERS"
    assert not epub_pipeline.cancel_epub_task("不存在"), "无任务时 cancel 返回 False"
    print("PASS: 非 epub / 无章节 / 无任务 cancel 错误分支")


async def main():
    await scenario_success()
    await scenario_fail_skip()
    await scenario_retry_recover()
    await scenario_all_fail()
    await scenario_summary_split()
    await scenario_resume()
    await scenario_cancel()
    await scenario_busy()
    scenario_errors()
    print("✅ 全部 epub_pipeline 测试通过")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        epub_pipeline._llm_factory = _ORIG_FACTORY
