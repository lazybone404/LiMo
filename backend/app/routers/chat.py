"""
对话路由 — POST /api/chat + GET /api/chat/stream

架构参照 Pi：System Prompt 分层 → ToolRegistry 动态注册 → SessionManager 会话管理
AgentRunner 核心循环不动，前端 API 完全兼容。
"""

import json
import logging
import asyncio

from fastapi import APIRouter, Request, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.llm import LLMService, AgentRunner
from app.core.errors import AppError
from app.core import logging as log
from app.core.text_utils import smart_truncate
from app.agent.tools import search_web, fetch_url
from app.agent.tool_registry import ToolRegistry, ToolDef
from app.agent.session import SessionManager
from app.agent.prompts import loader as prompt_loader

logger = logging.getLogger(__name__)
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
                "matches": matched,
                "totalLength": len(raw),
            }, ensure_ascii=False)
        return json.dumps({"title": tx["title"], "content": raw[:2000]}, ensure_ascii=False)
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


def _build_doc_context(store: object, doc_ids: list[str]) -> str:
    """构建文档上下文摘要（注入 system prompt）"""
    if not doc_ids:
        return "（未选中文档，可用 list_docs 查看可用文档）"
    parts = []
    for did in doc_ids:
        tx = store.get_transcript(did)
        if tx:
            parts.append(f"[{tx['title']}]\n{smart_truncate(tx['rawText'], 1000)}")
    return "\n\n".join(parts) if parts else "（未选中文档）"


# ── 路由 ──


@router.post("/chat")
async def chat(req: ChatRequest, request: Request) -> dict[str, object]:
    store = request.app.state.store
    config = request.app.state.config

    model_id = req.model_id
    if not model_id:
        model_id = config.get("scene_models", {}).get("chat")

    llm = LLMService(config, model_id=model_id)
    if not llm.api_key:
        raise AppError("CHAT_NO_API_KEY", "LLM API Key 未配置，请在设置中填写", recoverable=False)

    system = prompt_loader.build(_build_doc_context(store, req.doc_ids))

    agent = AgentRunner(llm, max_rounds=3)
    _build_registry(store).apply_to(agent)

    log.info("Chat start", question_len=len(req.question), docs=len(req.doc_ids))
    try:
        answer = await agent.run(system, req.question, req.history)
        log.info("Chat done", answer_len=len(answer))
        return {"answer": answer}
    except AppError:
        raise
    except Exception as e:
        log.exception("Chat failed", error=str(e)[:200])
        raise AppError("CHAT_FAILED", f"对话失败: {str(e)[:200]}") from e


@router.get("/chat/stream")
async def chat_stream(
    request: Request,
    question: str = Query(...),
    doc_ids: str = Query(""),
    model_id: int | None = None,
    conversation_id: str = Query(""),
):
    """SSE 流式对话 — 实时推送回答 + 工具调用进度 + 自动持久化"""
    store = request.app.state.store
    config = request.app.state.config

    doc_list = [d.strip() for d in doc_ids.split(",") if d.strip()]

    # ── 会话管理 ──
    sessions = SessionManager(store)
    conv = sessions.load_or_create(conversation_id, question, model_id, doc_list)
    conversation_id = conv["id"]
    history = sessions.get_history(conv)

    # ── 模型 ──
    if not model_id:
        model_id = config.get("scene_models", {}).get("chat")

    llm = LLMService(config, model_id=model_id)
    if not llm.api_key:
        raise AppError("CHAT_NO_API_KEY", "LLM API Key 未配置，请在设置中填写", recoverable=False)

    # ── System prompt + 工具注册 ──
    system = prompt_loader.build(_build_doc_context(store, doc_list))
    agent = AgentRunner(llm, max_rounds=3)
    _build_registry(store).apply_to(agent)

    # ── 保存用户消息（刷新不丢）──
    sessions.append_user_message(conversation_id, question)

    cancel_event = asyncio.Event()

    async def sse_stream():
        full_answer: list[str] = []
        async def _watch_disconnect():
            while not cancel_event.is_set():
                if await request.is_disconnected():
                    cancel_event.set()
                    return
                await asyncio.sleep(0.5)
        disconnect_task = asyncio.create_task(_watch_disconnect())
        try:
            yield f"data: {json.dumps({'type': 'meta', 'conversation_id': conversation_id}, ensure_ascii=False)}\n\n"

            async for event in agent.stream_run(system, question, history, cancel_event=cancel_event):
                if event["type"] == "text":
                    full_answer.append(event["text"])
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

            sessions.append_assistant_message(conversation_id, "".join(full_answer))
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)[:200]}, ensure_ascii=False)}\n\n"
        finally:
            if not disconnect_task.done():
                disconnect_task.cancel()

    return StreamingResponse(
        sse_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
