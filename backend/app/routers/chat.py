"""
对话路由 — POST /api/chat + GET /api/chat/stream

架构参照 Pi：System Prompt 分层 → ToolRegistry 动态注册 → SessionManager 会话管理
AgentRunner 核心循环不动，前端 API 完全兼容。
"""

import json
import asyncio

from fastapi import APIRouter, Request, Query, Depends
from app.core.dependencies import get_store, get_config

from app.core.disconnect import disconnect_watcher
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.llm import LLMService, AgentRunner
from app.core.errors import AppError
from app.core import logging as log
from app.core.chat_context import compute_budget, build_doc_context, trim_history
from app.core.token import estimate_tokens
from app.agent.tools import search_web, fetch_url
from app.agent.tool_registry import ToolRegistry, ToolDef
from app.agent.session import SessionManager
from app.agent.compress import should_compress, compress_history
from app.agent.prompts import loader as prompt_loader
from app.core.artifact_blocks import parse_blocks

router = APIRouter()


class ChatRequest(BaseModel):
    question: str
    doc_ids: list[str] = []
    history: list[dict] = []
    mode: str = "single"
    model_id: int | None = None


# ── Tool 工厂函数 ──

def _make_search_docs(store: object):
    def search_docs(query: str) -> str:
        results = store.list_transcripts({"query": query})
        if not results:
            return json.dumps({"found": 0, "results": []}, ensure_ascii=False)
        items = [
            {"id": r["id"], "title": r["title"], "sourceType": r["sourceType"]}
            for r in results[:5]
        ]
        return json.dumps({"found": len(items), "results": items}, ensure_ascii=False)
    return search_docs


def _make_lookup_doc(store: object):
    def lookup_doc(id: str, query: str = "") -> str:
        tx = store.get_transcript(id)
        if not tx:
            return json.dumps({"error": "not found"}, ensure_ascii=False)
        raw = tx.get("rawText", "")
        if query:
            lines = raw.split("\n")
            matched = [l for l in lines if query.lower() in l.lower()][:10]
            return json.dumps({
                "title": tx.get("title", ""),
                "docId": id,
                "matches": matched,
                "totalLength": len(raw),
            }, ensure_ascii=False)
        return json.dumps({
            "title": tx["title"],
            "docId": id,
            "content": raw[:2000],
        }, ensure_ascii=False)
    return lookup_doc


def _make_list_docs(store: object):
    def list_docs() -> str:
        docs = store.list_transcripts()
        items = [
            {"id": d["id"], "title": d["title"], "sourceType": d["sourceType"]}
            for d in docs
        ]
        return json.dumps(items, ensure_ascii=False)
    return list_docs


def _build_registry(store: object) -> ToolRegistry:
    """构建工具注册中心 — 参照 Pi 的扩展工具动态注册"""
    reg = ToolRegistry()
    reg.register(ToolDef(
        "search_docs", "跨所有文档搜索相关内容",
        {"type": "object", "properties": {"query": {"type": "string", "description": "搜索关键词"}}, "required": ["query"]},
        _make_search_docs(store),
    ))
    reg.register(ToolDef(
        "lookup_doc", "查看指定文档的完整内容",
        {"type": "object", "properties": {
            "id": {"type": "string", "description": "文档ID"},
            "query": {"type": "string", "description": "可选：在文档内搜索的关键词"},
        }, "required": ["id"]},
        _make_lookup_doc(store),
    ))
    reg.register(ToolDef(
        "list_docs", "列出当前知识库中的可用文档",
        {"type": "object", "properties": {}},
        _make_list_docs(store),
    ))
    reg.register(ToolDef(
        "search_web", "搜索互联网获取最新信息，返回 JSON 结果列表。当本地文档不足以回答时使用",
        {"type": "object", "properties": {"query": {"type": "string", "description": "搜索关键词"}}, "required": ["query"]},
        search_web,
    ))
    reg.register(ToolDef(
        "fetch_url", "抓取指定网页的正文内容，返回 Markdown。用于精读搜索结果中的网页",
        {"type": "object", "properties": {"url": {"type": "string", "description": "网页 URL"}}, "required": ["url"]},
        fetch_url,
    ))
    return reg


def _append_refs(refs: list[dict], seen: set[str], incoming: list | None) -> None:
    """把 `incoming` 里的引用按 `docId` **保序去重**后并入 `refs`（原地修改两个容器）。

    ⚠️ **必须去重**：同一篇文档被多个工具轮次引用是常态（`search_docs` 命中 →
    `lookup_doc` 打开），`extend` 会让同一 `docId` 进两次。前端引用 chips 用 `docId`
    当 React key —— 流式路径的 `onRef` 顺手去重了，**重开会话的恢复路径没有**，于是
    「落盘数据里的重复」只在下次打开会话时炸成 React 重复 key 警告 + chips 渲染错乱。

    抽成模块级函数是为了让测试**直调真实实现**：调用点在一个嵌套的 async generator
    里（`sse_stream`），测试够不到，只能复刻表达式 —— 而复刻的表达式正是最常见的
    「改了一处、另一处还在旧假设上」的来源。
    """
    for r in incoming or []:
        rid = r.get("docId")
        if rid and rid not in seen:
            seen.add(rid)
            refs.append(r)


# ── 路由 ──


@router.post("/chat")
async def chat(req: ChatRequest, request: Request, store=Depends(get_store), config=Depends(get_config)) -> dict[str, object]:

    model_id = req.model_id
    if not model_id:
        model_id = config.get("scene_models", {}).get("chat")

    llm = LLMService(config, model_id=model_id)
    if not llm.api_key:
        raise AppError("CHAT_NO_API_KEY", "LLM API Key 未配置，请在设置中填写", recoverable=False)

    # 上下文预算组装（C2）
    _, budget = compute_budget(config, model_id)
    cc = config.get("chat_context") or {}
    doc_text, _ = build_doc_context(store, req.doc_ids, budget, cc.get("doc_mode", "auto"))
    system = prompt_loader.build(doc_text)
    history = trim_history(req.history or [], budget, history_limit=cc.get("history_limit", 0))

    agent = AgentRunner(llm, max_rounds=3)
    _build_registry(store).apply_to(agent)

    log.info("Chat start", question_len=len(req.question), docs=len(req.doc_ids))
    try:
        answer = await agent.run(system, req.question, history)
        log.info("Chat done", answer_len=len(answer))
        return {"answer": answer}
    except AppError:
        raise
    except Exception as e:
        log.exception("Chat failed", error=str(e)[:200])
        raise AppError("CHAT_FAILED", f"对话失败: {str(e)[:200]}") from e
    finally:
        # 关闭 LLM 连接池（防 httpx client 常驻累积）
        try:
            await llm.aclose()
        except Exception:
            pass


@router.get("/chat/stream")
async def chat_stream(
    request: Request,
    question: str = Query(...),
    doc_ids: str = Query(""),
    doc_refs: str = Query(""),
    model_id: int | None = None,
    conversation_id: str = Query(""),
    store=Depends(get_store),
    config=Depends(get_config),
):
    """SSE 流式对话 — 实时推送回答 + 工具调用进度 + 自动持久化"""

    doc_list = [d.strip() for d in doc_ids.split(",") if d.strip()]

    # 本条消息 @ 引用的文档（JSON 数组 [{docId,title}]）→ 存 user 消息供展示
    doc_ref_list: list[dict] = []
    if doc_refs.strip():
        try:
            doc_ref_list = json.loads(doc_refs)
            if not isinstance(doc_ref_list, list):
                doc_ref_list = []
        except json.JSONDecodeError:
            doc_ref_list = []

    # ── 会话管理 ──
    sessions = SessionManager(store)
    conv = sessions.load_or_create(conversation_id, question, model_id, doc_list)
    conversation_id = conv["id"]

    # ── 模型 ──
    if not model_id:
        model_id = config.get("scene_models", {}).get("chat")

    llm = LLMService(config, model_id=model_id)
    if not llm.api_key:
        raise AppError("CHAT_NO_API_KEY", "LLM API Key 未配置，请在设置中填写", recoverable=False)

    # ── 工具注册 + 上下文预算组装（C2：预算 = 窗口×0.7，分配 system→doc→history）──
    agent = AgentRunner(llm, max_rounds=3)
    _build_registry(store).apply_to(agent)

    window, budget = compute_budget(config, model_id)
    cc = config.get("chat_context") or {}
    log.debug("Chat 预算分配", window=window, budget=budget, doc_mode=cc.get("doc_mode", "auto"))

    # C3 历史压缩：超阈值 LLM 摘要早期消息（压缩模型不可用/失败 → 跳过，不阻塞对话）
    compressed_count = 0
    try:
        if should_compress(conv, budget):
            log.info("Chat 触发历史压缩", conversation_id=conversation_id,
                     msgs=len(conv.get("messages", [])), budget=budget)
            _new_summary, compressed_count = await compress_history(config, store, conv, model_id, budget)
    except Exception as e:
        log.warning("压缩检查异常，跳过", error=str(e)[:120])
        compressed_count = 0

    doc_text, _ = build_doc_context(store, doc_list, budget, cc.get("doc_mode", "auto"))
    log.debug("Chat 文档注入", docs=len(doc_list), chars=len(doc_text), mode=cc.get("doc_mode", "auto"))
    summary = conv.get("summary") or ""
    system_text = "\n\n".join(
        x for x in [doc_text, f"[历史摘要]\n{summary}" if summary else None] if x)
    system = prompt_loader.build(system_text)
    tool_defs = agent._tool_defs
    used = estimate_tokens(system) + sum(estimate_tokens(json.dumps(t, ensure_ascii=False)) for t in tool_defs)
    hist_budget = max(0, budget - used)
    # clear 点：该索引前的消息不再携带（C4 clearContext 语义）
    clear_at = conv.get("clear_context_at") or 0
    msgs = conv.get("messages", [])[clear_at:]
    raw_history = trim_history(msgs, hist_budget, history_limit=cc.get("history_limit", 0))
    history = [{"role": m["role"], "content": m["content"]} for m in raw_history]
    log.debug("Chat 历史裁剪", raw=len(msgs), kept=len(raw_history),
              hist_budget=hist_budget, limit=cc.get("history_limit", 0))

    # 模型价格（C4 费用显示；未配置则 None，前端不显示费用）
    price_in = price_out = None
    for m in config.get("enabled_models", []):
        if m.get("id") == model_id:
            price_in = m.get("price_in")
            price_out = m.get("price_out")
            break

    # ── 保存用户消息（刷新不丢）──
    sessions.append_user_message(conversation_id, question, doc_refs=doc_ref_list or None)

    cancel_event = asyncio.Event()

    async def sse_stream():
        full_answer: list[str] = []
        tool_calls: list[dict] = []
        # 引用累加器：`seen_ref_ids` 与 `refs` 成对（去重判据见 `_append_refs`）
        refs: list[dict] = []
        seen_ref_ids: set[str] = set()
        thinking_parts: list[str] = []
        async with disconnect_watcher(request, cancel_event):
            try:
                if compressed_count:
                    yield f"data: {json.dumps({'type': 'compressed', 'count': compressed_count}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'meta', 'conversation_id': conversation_id, 'context_window': window, 'budget': budget, 'price_in': price_in, 'price_out': price_out}, ensure_ascii=False)}\n\n"

                async for event in agent.stream_run(system, question, history, cancel_event=cancel_event):
                    if event["type"] == "text":
                        full_answer.append(event["text"])
                    elif event["type"] == "tool_start":
                        tool_calls.append({"tool": event["tool"], "display": event["display"]})
                    elif event["type"] == "tool_result":
                        _append_refs(refs, seen_ref_ids, event.get("refs"))
                    elif event["type"] == "thinking":
                        thinking_parts.append(event.get("text", ""))
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

                sessions.append_assistant_message(
                    conversation_id,
                    "".join(full_answer),
                    doc_refs=refs or None,
                    tool_calls=tool_calls or None,
                    thinking="".join(thinking_parts) or None,
                )
                # ⚠️ **`blocks` 是本轮唯一不靠肉眼判断的模型行为信号**：`limo-block` 卡片组 /
                # 追问建议是**模型可选**的输出，遵循率与滥用率都只能在真实流量里看。
                # 没有这一项，「模型到底用没用」就只能翻聊天记录猜。
                # ⚠️ 用 `parse_blocks` 计数而不是数子串：正文里讨论本协议时也会出现
                # `limo-block` 这个词，那不算用了它。
                answer = "".join(full_answer)
                blocks = [s for s in parse_blocks(answer) if s["kind"] == "block"]
                log.info("Chat SSE 完成", conversation_id=conversation_id,
                         answer_len=len(answer),
                         blocks=len(blocks),
                         block_types=",".join(sorted({s["type"] for s in blocks})) or "-",
                         tools=len(tool_calls), refs=len(refs), saved=True)
            finally:
                # 关闭 LLM 连接池（防 httpx client 常驻累积）
                try:
                    await llm.aclose()
                except Exception:
                    pass

    return StreamingResponse(
        sse_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
