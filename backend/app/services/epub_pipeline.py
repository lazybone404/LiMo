"""
电子书一键全解读管道 — 逐章解读 + 归并全书总结 + 中断续跑

阶段 1：逐章串行解读（deep=False 用章节骨架省 token，deep=True 用全文），
        每章成功即落盘片段 {doc}/epub_chapters/{位置}.md —— 逐章解读的唯一写点。
阶段 2：全部章处理完（成功 ∪ 失败）→ 读成功章片段归并为全书总结 →
        一次性落盘 notes.md（全书总结）+ polished.md（逐章解读，failed 章标注）。
        完成条件 = 全部章处理过，无论是否有失败章 —— 产物必定落盘，永不因单章失败卡死。

中断续跑：epub_task.json 落盘状态 + 片段文件双保险。
取消唯一入口 = POST /api/epub/cancel（软取消：cancel_event 置位，当前章 LLM 调用
逐 chunk 检查提前返回）。不挂 disconnect_watcher —— 打包端关窗即中断，续跑靠落盘检测。

关键约定：
- state.chapters_done / chapters_failed 存 chapters 数组的**位置索引**（0..N-1），
  与片段文件名一致（{i}.md），list_epub_chapters 按文件名升序 = 书序。
- LLM 工厂 _llm_factory(config, model_id) 可替换 —— 测试注入 FakeLLM。
- 模型在任务启动时快照（LLMService 构造时解析到实例属性），中途改配置不影响进行中任务。
"""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Optional

from app.core.text_utils import split_into_chunks
from app.core.types import GenerateMode, MarkdownView, Style
from app.generation.prompts.common import PromptComposer
from app.llm import LLMService, CancelledError
from app.services.errors import EpubError

logger = logging.getLogger(__name__)

# ── 调参 ──
CHAPTER_TEMPERATURE = 0.3          # 逐章解读：忠实提炼，温度偏低
CHAPTER_RETRY = 2                  # 单章失败重试次数（加上首次尝试 = 3 次）
SUMMARY_BUDGET_CHARS = 24000       # 阶段 2 单轮归并预算（字符），超过则分组 map-reduce
SUMMARY_TEMPERATURE = 0.4          # 归并总结：适度创造
SUMMARY_RETRY = 2
CALL_MAX_TOKENS = 65536            # 对齐 LLMService.MAX_TOKENS
SKELETON_CHARS_PER_SECTION = 1500  # 骨架输入单节安全阀（防异常超长节）
EMPTY_CHAPTER_PLACEHOLDER = "> 本章无可用文字内容（可能为纯图片/扫描页），已自动跳过。"


@dataclass
class EpubTask:
    doc_id: str
    state: dict
    cancel_event: asyncio.Event


# 内存任务表：doc_id → EpubTask。任务结束（完成/取消/异常）时移除；
# 状态恢复依赖落盘的 epub_task.json + 片段文件（重启后由前端检测触发续跑）。
_tasks: dict[str, EpubTask] = {}


def _llm_factory(config: dict, model_id: Optional[int]) -> LLMService:
    """LLM 工厂 — 测试替换此函数注入 FakeLLM，比 monkeypatch 类方法稳。"""
    return LLMService(config, model_id=model_id)


# ── Prompt 构建 ──


def _build_chapter_input(chapter: dict, deep: bool) -> str:
    """构建单章输入文本。deep=False 用骨架字段（省 ~70% token），deep=True 用全文。"""
    sections = chapter.get("sections") or []
    parts = []
    for s in sections:
        s_title = s.get("title", "")
        if deep:
            text = (s.get("text") or "").strip()
            if text:
                parts.append(f"### {s_title}\n{text}")
        else:
            sk = (s.get("skeleton") or s.get("text") or "").strip()
            if sk:
                parts.append(f"### {s_title}\n{sk[:SKELETON_CHARS_PER_SECTION]}")
    if not parts:
        # 兜底：sections 缺失/为空时直接用整章文本
        whole = (chapter.get("text") or "").strip()
        if whole:
            return whole
        return ""
    return "\n\n".join(parts)


def _build_chapter_prompt(book_title: str, chapter: dict, deep: bool) -> Optional[str]:
    """构建单章解读 prompt。章节无可用文字时返回 None（调用方写占位跳过）。"""
    source = _build_chapter_input(chapter, deep)
    if not source.strip():
        return None
    kind = "全文" if deep else "章节骨架（标题 + 首段 + 重点 + 列表 + 表格引用）"
    task = (
        f"以下是电子书《{book_title}》第 {chapter.get('index', '?')} 章"
        f"「{chapter.get('title', '未命名')}」的{kind}。\n"
        "请对该章节进行**解读**，输出结构化的 Markdown：\n"
        "1. **本章概述**：3-5 句话概括这一章讲了什么。\n"
        "2. **要点提炼**：用 bullet 列出关键论点、概念、数据。\n"
        "3. **金句摘录**（可选）：值得摘录的原句或关键信息。\n"
        "解读 500-800 字，忠于原文，不臆造原文没有的内容。"
    )
    sections = [
        ("role", "你是一个专业的图书阅读助手，擅长逐章解读电子书，提炼要点并给出清晰概述。"),
        ("task", task),
        ("source_content", f"章节内容：\n{source.strip()}"),
        ("hard_constraints", "忠于原文，不臆造；不输出代码块包裹；解读控制在 500-800 字。"),
        ("output_format", "仅返回最终的 Markdown 内容（以 `## 本章概述` 开头），不要包裹在代码块中。"),
    ]
    return PromptComposer.compose(sections)


def _build_summary_prompt(book_title: str, source: str, is_reduce: bool = False) -> str:
    task_header = "以下是电子书《%s》的章节解读归纳，请据此撰写全书导读总结。" % book_title
    if is_reduce:
        task_header = "以下是电子书《%s》的若干子总结，请归并撰写一份完整全书导读总结。" % book_title
    task = (
        f"{task_header}\n"
        "输出结构化的 Markdown：\n"
        "1. **书籍简介**：1-2 句话说明这本书讲什么。\n"
        "2. **主线脉络**：全书讲了什么、逻辑结构（按章节串联）。\n"
        "3. **核心观点**：本书最重要的 3-7 个观点/主题。\n"
        "4. **关键实体**：重要人物/术语/概念（按书类型自行决定人物或术语）。\n"
        "5. **适合人群**：哪些读者适合读这本书。\n"
        "总结要全面反映全书，忠于章节解读内容，不臆造。"
    )
    sections = [
        ("role", "你是一个专业的图书导读作者，擅长基于逐章解读撰写全书导读总结。"),
        ("task", task),
        ("source_content", f"章节解读内容：\n{source.strip()}"),
        ("hard_constraints", "忠于解读内容，不臆造；不输出代码块包裹。"),
        ("output_format",
         "仅返回最终的 Markdown 内容（以 `# 《书名》导读` 一级标题开头），不要包裹在代码块中。"),
    ]
    return PromptComposer.compose(sections)


def _build_sub_summary_prompt(book_title: str, group: int, total: int, chunk: str) -> str:
    task = (
        f"以下是一组章节解读（第 {group}/{total} 组）来自电子书《{book_title}》。\n"
        "请归纳为一份 300-500 字的**子总结**，覆盖该组章节的核心内容和主线进展，"
        "供后续归并为全书导读。"
    )
    sections = [
        ("role", "你是一个专业的图书阅读助手，擅长归纳章节解读。"),
        ("task", task),
        ("source_content", f"章节解读：\n{chunk.strip()}"),
        ("hard_constraints", "忠于解读内容，不臆造；不输出代码块包裹。"),
        ("output_format", "仅返回最终的 Markdown 子总结内容，不要包裹在代码块中。"),
    ]
    return PromptComposer.compose(sections)


# ── 状态管理 ──


def _load_or_init_state(store, doc_id: str, total: int,
                        model_id: Optional[int], deep: bool) -> dict:
    """加载已有任务状态或初始化。续跑保留进度；全新/已完成则清空片段整轮重跑。"""
    existing = store.load_epub_task(doc_id)
    if (existing and existing.get("stage") != "done"
            and isinstance(existing.get("chapters_done"), list)):
        # 续跑：保留进度；模型/深度用新请求（用户换模型 → 后半风格不同，已确认可接受）
        existing["model_id"] = model_id
        existing["deep"] = deep
        existing.pop("current_title", None)
        return existing
    # 全新 / 已完成（整轮重跑）：清空片段 + 重置状态
    store.clear_epub_chapters(doc_id)
    return {
        "stage": "chapters",
        "chapters_done": [],
        "chapters_failed": [],
        "total_chapters": total,
        "model_id": model_id,
        "deep": deep,
        "current_title": "",
    }


def _persist(store, doc_id: str, state: dict) -> None:
    try:
        store.save_epub_task(doc_id, state)
    except Exception as e:
        logger.warning("epub 任务状态落盘失败: %s", str(e)[:120])


# ── 任务生命周期 ──


def start_epub_task(store, config: dict, doc_id: str,
                    model_id: Optional[int] = None, deep: bool = False) -> dict:
    """启动/续跑 epub 解读任务（同步创建后台 task，立即返回）。"""
    if doc_id in _tasks:
        raise EpubError("EPUB_BUSY", "该文档正在解读中，请等待完成或取消后重试")
    tx = store.get_transcript(doc_id)
    if not tx:
        raise EpubError("EPUB_TX_NOT_FOUND", "文档不存在", detail=doc_id)
    if tx.get("sourceType") != "epub":
        raise EpubError("EPUB_NOT_EPUB", "该文档不是电子书，无法进行章节解读")
    chapters = _parse_chapters(tx.get("rawText", ""))
    if not chapters:
        raise EpubError("EPUB_NO_CHAPTERS", "未解析到电子书章节，无法解读")

    if model_id is None:
        model_id = config.get("scene_models", {}).get("generate")
    # LLMService 构造时快照模型配置（解析到实例属性），中途改配置不影响进行中任务
    llm = _llm_factory(config, model_id)

    state = _load_or_init_state(store, doc_id, len(chapters), model_id, deep)
    cancel_event = asyncio.Event()
    task = asyncio.create_task(
        _run_epub_task(store, llm, doc_id, chapters,
                       tx.get("title", ""), state, cancel_event)
    )
    _tasks[doc_id] = EpubTask(doc_id=doc_id, state=state, cancel_event=cancel_event)
    logger.info("epub 解读启动: %s total=%d deep=%s model=%s",
                doc_id, len(chapters), deep, model_id)
    return {"started": True, "total_chapters": len(chapters)}


async def _run_epub_task(store, llm, doc_id: str, chapters: list, book_title: str,
                         state: dict, cancel_event: asyncio.Event) -> None:
    try:
        # ── 阶段 1：逐章串行（跳过已成功，重试失败）──
        for i, ch in enumerate(chapters):
            if cancel_event.is_set():
                break
            if i in state["chapters_done"]:
                continue
            state["current_title"] = ch.get("title", "")
            _persist(store, doc_id, state)

            ok = await _interpret_chapter(store, llm, doc_id, i, ch,
                                          book_title, state, cancel_event)
            if ok:
                if i in state["chapters_failed"]:
                    state["chapters_failed"].remove(i)
                if i not in state["chapters_done"]:
                    state["chapters_done"].append(i)
            else:
                if i not in state["chapters_failed"]:
                    state["chapters_failed"].append(i)
            state["current_title"] = ""
            _persist(store, doc_id, state)

        if cancel_event.is_set():
            logger.info("epub 解读已取消（保留进度）: %s done=%d failed=%d",
                        doc_id, len(state["chapters_done"]), len(state["chapters_failed"]))
            return  # 取消：保留进度，不入阶段 2

        # ── 阶段 2：全部章处理完 → 归并全书总结 → 一次性落盘 ──
        state["stage"] = "summary"
        _persist(store, doc_id, state)
        await _finalize(store, llm, doc_id, chapters, book_title, state, cancel_event)
    except Exception as e:
        logger.exception("epub 解读管道异常: %s", str(e)[:200])
    finally:
        _tasks.pop(doc_id, None)


async def _interpret_chapter(store, llm, doc_id: str, i: int, ch: dict,
                             book_title: str, state: dict,
                             cancel_event: asyncio.Event) -> bool:
    """解读单章。失败重试 CHAPTER_RETRY 次。空章节写占位片段直接成功。"""
    prompt = _build_chapter_prompt(book_title, ch, state.get("deep", False))
    if prompt is None:
        # 章节无可用文字 → 占位片段（记成功，不消耗 LLM），保证阶段 2 可推进
        store.save_epub_chapter(doc_id, i, ch.get("title", ""), EMPTY_CHAPTER_PLACEHOLDER)
        return True

    for attempt in range(CHAPTER_RETRY + 1):
        if cancel_event.is_set():
            return False
        try:
            content = await llm.call(
                prompt,
                max_tokens=CALL_MAX_TOKENS,
                temperature=CHAPTER_TEMPERATURE,
                max_continuations=0,
                cancel_event=cancel_event,
            )
            content = content.strip()
            if not content:
                raise EpubError("EPUB_EMPTY_OUTPUT", "第 %d 章解读结果为空" % (i + 1))
            store.save_epub_chapter(doc_id, i, ch.get("title", ""), content)
            logger.info("epub 第 %d 章解读完成 (%d chars)", i + 1, len(content))
            return True
        except CancelledError:
            logger.info("epub 第 %d 章被取消", i + 1)
            return False
        except Exception as e:
            if cancel_event.is_set():
                return False
            if attempt < CHAPTER_RETRY:
                logger.warning("epub 第 %d 章解读失败（第 %d 次），重试: %s",
                               i + 1, attempt + 1, str(e)[:120])
                continue
            logger.error("epub 第 %d 章解读失败（重试 %d 次后放弃）: %s",
                         i + 1, CHAPTER_RETRY, str(e)[:120])
            return False
    return False


async def _finalize(store, llm, doc_id: str, chapters: list, book_title: str,
                    state: dict, cancel_event: asyncio.Event) -> None:
    """归并全书总结 + 组装产物 + 一次性落盘 notes/polished（两版制 = 新版本）。"""
    done_parts = store.list_epub_chapters(doc_id)

    # polished = 按书序交错：成功章片段 + failed 章标注
    lines, d = [], 0
    for i, ch in enumerate(chapters):
        if i in state["chapters_failed"]:
            lines.append(f"> 第 {i + 1} 章「{ch.get('title', '未命名')}」解读失败，已跳过（可重新解读）。\n")
        elif d < len(done_parts):
            lines.append(done_parts[d])
            d += 1
    polished = "\n\n".join(lines).strip()

    # 阶段 2 归并全书总结（失败 → notes 写明确失败说明，不静默）
    summary = await _summarize_book(llm, book_title, done_parts, cancel_event)
    if summary is None:
        summary = (
            f"# 《{book_title}》导读\n\n"
            f"> 全书总结生成失败：{len(state['chapters_done'])} 章解读成功，"
            f"但阶段 2 归并未能完成。\n"
            f"> 可查看整理版原文中的逐章解读，或重新发起一键解读。"
        )

    store.save_markdown(MarkdownView(
        id=f"md_{uuid.uuid4().hex[:12]}", transcript_id=doc_id,
        content=summary, style=Style.DETAILED, mode=GenerateMode.NOTES,
    ))
    store.save_markdown(MarkdownView(
        id=f"md_{uuid.uuid4().hex[:12]}", transcript_id=doc_id,
        content=polished, style=Style.DETAILED, mode=GenerateMode.POLISHED,
    ))
    # 自动标题：epub 导入标题一般为文件名，基本不触发；触发时用总结标题（更贴近书主题）
    try:
        from app.routers.generate import _auto_title
        _auto_title(store, doc_id, summary)
    except Exception:
        pass

    state["stage"] = "done"
    _persist(store, doc_id, state)
    logger.info("epub 解读完成: %s done=%d failed=%d chars=%d",
                doc_id, len(state["chapters_done"]), len(state["chapters_failed"]), len(polished))


async def _summarize_book(llm, book_title: str, done_parts: list[str],
                          cancel_event: asyncio.Event) -> Optional[str]:
    """阶段 2：归并全书总结。返回总结文本，失败返回 None。"""
    source = "\n\n".join(done_parts).strip()
    if not source:
        return None  # 全部章失败，无归并输入
    try:
        if len(source) <= SUMMARY_BUDGET_CHARS:
            return await _llm_retry(llm, _build_summary_prompt(book_title, source), cancel_event)
        # 超限：分组子总结 → 归并（map-reduce）
        chunks = split_into_chunks(source, max_chars=SUMMARY_BUDGET_CHARS)
        subs = []
        for i, chunk in enumerate(chunks, start=1):
            if cancel_event.is_set():
                return None
            sub = await _llm_retry(
                llm, _build_sub_summary_prompt(book_title, i, len(chunks), chunk), cancel_event)
            if sub is None:
                return None
            subs.append(sub)
        merged = "\n\n".join(subs)
        return await _llm_retry(llm, _build_summary_prompt(book_title, merged, is_reduce=True),
                                cancel_event)
    except Exception as e:
        logger.error("epub 阶段 2 归并失败: %s", str(e)[:120])
        return None


async def _llm_retry(llm, prompt: str, cancel_event: asyncio.Event,
                     retries: int = SUMMARY_RETRY) -> Optional[str]:
    """LLM 调用 + 重试。取消/重试耗尽返回 None。"""
    for attempt in range(retries + 1):
        try:
            if cancel_event.is_set():
                return None
            content = await llm.call(prompt, max_tokens=CALL_MAX_TOKENS,
                                     temperature=SUMMARY_TEMPERATURE,
                                     max_continuations=0, cancel_event=cancel_event)
            return content.strip()
        except CancelledError:
            return None
        except Exception as e:
            if cancel_event.is_set() or attempt >= retries:
                return None
            logger.warning("归并调用失败（第 %d 次），重试: %s", attempt + 1, str(e)[:120])
    return None


# ── 查询 / 取消 ──


def get_epub_task(store, doc_id: str) -> dict:
    """查询 epub 任务状态。内存态优先，其次落盘。"""
    mem = _tasks.get(doc_id)
    state = mem.state if mem else store.load_epub_task(doc_id)
    if state is None:
        return {
            "running": bool(mem), "stage": None,
            "chapters_done": [], "chapters_failed": [], "total_chapters": 0,
            "current_title": "", "remaining_chapters": 0,
            "deep": False, "model_id": None,
        }
    total = state.get("total_chapters", 0)
    done = len(state.get("chapters_done", []))
    return {
        "running": doc_id in _tasks,
        "stage": state.get("stage"),
        "chapters_done": state.get("chapters_done", []),
        "chapters_failed": state.get("chapters_failed", []),
        "total_chapters": total,
        "current_title": state.get("current_title") or "",
        "remaining_chapters": max(total - done, 0),
        "deep": state.get("deep", False),
        "model_id": state.get("model_id"),
    }


def cancel_epub_task(doc_id: str) -> bool:
    """软取消：置 cancel_event，当前章 LLM 调用逐 chunk 检查提前返回。"""
    task = _tasks.get(doc_id)
    if not task:
        return False
    task.cancel_event.set()
    logger.info("epub 解读取消请求: %s", doc_id)
    return True


def _parse_chapters(raw_text: str) -> list:
    """解析 EpubEngine 转录的章节 JSON。返回章节列表（按书序）。"""
    if not raw_text:
        return []
    try:
        data = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        logger.warning("epub rawText 不是合法 JSON，无法解析章节")
        return []
    chapters = data.get("chapters")
    return chapters if isinstance(chapters, list) else []
