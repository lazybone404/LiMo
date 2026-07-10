"""
对话路由 — POST /api/chat + GET /api/chat/stream
"""

import json
import logging
import uuid
from datetime import datetime
import asyncio

from fastapi import APIRouter, Request, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.llm import LLMService, AgentRunner
from app.core.errors import AppError
from app.core.types import ChatMessage, Conversation
from app.core import logging as log
from app.agent.tools import search_web, fetch_url

logger = logging.getLogger(__name__)
router = APIRouter()


class ChatRequest(BaseModel):
    question: str
    doc_ids: list[str] = []
    history: list[dict] = []
    mode: str = "single"
    model_id: int | None = None


SYSTEM_PROMPT = """你是知识库问答助手。你可以查询已导入的文档来回答问题。
如果本地文档不足以回答，可使用 search_web 搜索互联网获取最新信息，必要时用 fetch_url 抓取网页全文精读。

回答要求：
- 基于文档或网络搜索结果回答，标注来源
- 如果所有来源都不足以回答，诚实说明
- 用中文回答，简洁准确"""


def make_search_fn(store: object):
    """跨文档关键词检索"""
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


def make_lookup_fn(store: object):
    """查指定文档全文"""
    def lookup_doc(id: str, query: str = "") -> str:
        tx = store.get_transcript(id)
        if not tx:
            return json.dumps({"error": "not found"}, ensure_ascii=False)
        raw = tx.get("rawText", "")
        # 简单关键词匹配找相关段落
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


def make_list_fn(store: object):
    """列出可用文档（当前会话）"""
    def list_docs() -> str:
        docs = store.list_transcripts()
        items = [
            {"id": d["id"], "title": d["title"], "sourceType": d["sourceType"]}
            for d in docs
        ]
        return json.dumps(items, ensure_ascii=False)
    return list_docs


def _register_chat_tools(agent: object, store: object) -> None:
    """注册对话中可用的 5 个工具（/chat 和 /chat/stream 共用）"""
    agent.register_tool(
        "search_docs", "跨所有文档搜索相关内容",
        {"type": "object", "properties": {"query": {"type": "string", "description": "搜索关键词"}}, "required": ["query"]},
        make_search_fn(store),
    )
    agent.register_tool(
        "lookup_doc", "查看指定文档的完整内容",
        {"type": "object", "properties": {
            "id": {"type": "string", "description": "文档ID"},
            "query": {"type": "string", "description": "可选：在文档内搜索的关键词"},
        }, "required": ["id"]},
        make_lookup_fn(store),
    )
    agent.register_tool(
        "list_docs", "列出当前知识库中的可用文档",
        {"type": "object", "properties": {}},
        make_list_fn(store),
    )
    agent.register_tool(
        "search_web", "搜索互联网获取最新信息，返回 JSON 结果列表。当本地文档不足以回答时使用",
        {"type": "object", "properties": {"query": {"type": "string", "description": "搜索关键词"}}, "required": ["query"]},
        search_web,
    )
    agent.register_tool(
        "fetch_url", "抓取指定网页的正文内容，返回 Markdown。用于精读搜索结果中的网页",
        {"type": "object", "properties": {"url": {"type": "string", "description": "网页 URL"}}, "required": ["url"]},
        fetch_url,
    )


@router.post("/chat")
async def chat(req: ChatRequest, request: Request) -> dict[str, object]:
    store = request.app.state.store
    config = request.app.state.config

    # 解析模型：优先用请求中的 model_id，其次用 scene_bindings.chat
    model_id = req.model_id
    if not model_id:
        model_id = config.get("scene_models", {}).get("chat")

    llm = LLMService(config, model_id=model_id)
    if not llm.api_key:
        raise AppError("CHAT_NO_API_KEY", "LLM API Key 未配置，请在设置中填写", recoverable=False)

    # 构建 system prompt（含初始检索上下文）
    context_parts = []
    if req.doc_ids:
        for did in req.doc_ids:
            tx = store.get_transcript(did)
            if tx:
                context_parts.append(f"[{tx['title']}]\n{tx['rawText'][:1000]}")
    context = "\n\n".join(context_parts) if context_parts else "（未选中文档，可用 list_docs 查看可用文档）"

    system = SYSTEM_PROMPT + f"\n\n--- 当前文档 ---\n{context}"

    agent = AgentRunner(llm, max_rounds=3)
    _register_chat_tools(agent, store)

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

    # ── 对话持久化：加载或创建 ──
    conv = None
    if conversation_id:
        conv = store.get_conversation(conversation_id)
    if not conv:
        # 自动创建新对话
        conv_data = Conversation(
            id=f"conv_{uuid.uuid4().hex[:12]}",
            title=question[:60] + ("..." if len(question) > 60 else ""),
            model_id=model_id,
            doc_ids=doc_list,
        )
        store.save_conversation(conv_data)
        conv = conv_data.to_dict()
        conversation_id = conv["id"]

    # ── 从对话消息读取历史上下文 ──
    history = []
    for m in conv.get("messages", [])[-40:]:  # 最近 40 条 = 20 轮对话
        history.append({"role": m["role"], "content": m["content"]})

    # ── 解析 model_id ──
    if not model_id:
        model_id = config.get("scene_models", {}).get("chat")

    llm = LLMService(config, model_id=model_id)
    if not llm.api_key:
        raise AppError("CHAT_NO_API_KEY", "LLM API Key 未配置，请在设置中填写", recoverable=False)

    # 构建 system prompt
    context_parts = []
    for did in doc_list:
        tx = store.get_transcript(did)
        if tx:
            context_parts.append(f"[{tx['title']}]\n{tx['rawText'][:1000]}")
    context = "\n\n".join(context_parts) if context_parts else "（未选中文档）"
    system = SYSTEM_PROMPT + f"\n\n--- 当前文档 ---\n{context}"

    agent = AgentRunner(llm, max_rounds=3)
    _register_chat_tools(agent, store)

    cancel_event = asyncio.Event()

    # ── 立即保存用户消息（刷新不丢）──
    user_msg = ChatMessage(id=f"msg_{uuid.uuid4().hex[:12]}", role="user", content=question)
    conv_msg = store.get_conversation(conversation_id)
    if conv_msg:
        conv_msg.setdefault("messages", []).append(user_msg.to_dict())
        conv_msg["updated_at"] = datetime.now().isoformat()
        if conv_msg["title"] == question[:60] + ("..." if len(question) > 60 else "") or conv_msg["title"] == "新对话":
            conv_msg["title"] = question[:60] + ("..." if len(question) > 60 else "")
        store.save_conversation(conv_msg)

    async def sse_stream():
        full_answer: list[str] = []
        # 客户端断连监听 → cancel_event
        async def _watch_disconnect():
            while not cancel_event.is_set():
                if await request.is_disconnected():
                    cancel_event.set()
                    return
                await asyncio.sleep(0.5)
        disconnect_task = asyncio.create_task(_watch_disconnect())
        try:
            # 首条事件：返回 conversation_id
            yield f"data: {json.dumps({'type': 'meta', 'conversation_id': conversation_id}, ensure_ascii=False)}\n\n"

            async for event in agent.stream_run(system, question, history, cancel_event=cancel_event):
                if event["type"] == "text":
                    full_answer.append(event["text"])
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

            # 流式完成后保存 AI 回复
            ai_msg = ChatMessage(id=f"msg_{uuid.uuid4().hex[:12]}", role="assistant", content="".join(full_answer))
            conv_msg2 = store.get_conversation(conversation_id)
            if conv_msg2:
                conv_msg2.setdefault("messages", []).append(ai_msg.to_dict())
                conv_msg2["updated_at"] = datetime.now().isoformat()
                store.save_conversation(conv_msg2)
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
