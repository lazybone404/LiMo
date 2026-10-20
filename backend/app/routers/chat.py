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
from app.core.origin import ensure_trusted_request
from app.core import logging as log
from app.core.chat_context import compute_budget, build_doc_context, trim_history
from app.core.token import estimate_tokens
from app.agent.tools import search_web, fetch_url
from app.agent.tool_registry import ToolRegistry, ToolDef
from app.agent.session import SessionManager
from app.agent.compress import should_compress, compress_history
from app.agent.followup import followup_payload, generate_followups, should_attempt
from app.agent.prompts import loader as prompt_loader
from app.core.artifact_blocks import parse_blocks

router = APIRouter()


class ChatRequest(BaseModel):
    question: str
    doc_ids: list[str] = []
    history: list[dict] = []
    mode: str = "single"
    model_id: int | None = None
    answer_mode: str = "strict"


_ANSWER_MODES = ("strict", "open", "free")

# ⚠️ **同一份枚举的第二处落点就在 `agent/prompts/__init__.py` 的 `scopes` 字典里**
# （那边决定加载哪份 `scope_*.md`）。两处必须同键集：只加一边的失效形态是
# 「模板写了但请求 400」或「请求通了、加载到的是 `None`→ValueError」，两种都不在
# 正常路径上出现，直到用户真的选那一档。反向 import（prompts → routers）会成环，
# 故在这里做模块级体检 —— 键集分叉时**导入即炸**，比留到运行时好。


def _check_scope_keys() -> None:
    """`_ANSWER_MODES` 与 prompt 层的 `scopes` 键集必须相等（对照见 test_chat_answer_mode.py）。"""
    if set(_ANSWER_MODES) != set(prompt_loader.scopes):
        raise RuntimeError(
            f"answer_mode 枚举与 prompt 层分叉：{sorted(_ANSWER_MODES)} != "
            f"{sorted(prompt_loader.scopes)} —— 改档位时两处必须一起改")


_check_scope_keys()   # 导入即体检（见上一条注释）


def _answer_mode(value: str) -> str:
    """校验「回答依据」开关的取值 —— 入口严、读回宽。

    ⚠️ **非法值不静默退化成 `strict`**：那会让「用户开了发散，产物上却看不出任何
    区别」（严格模式的回答与发散模式在前几轮可能长得一样），是**零报错**的失效。
    判据同 `normalize_reader_intent`：请求入口对非法值报错，读历史时才对 `None`/
    未知值回退默认。三档只有三个合法值，显式报错不增加任何前端负担。
    """
    if value not in _ANSWER_MODES:
        raise AppError(
            "CHAT_BAD_ANSWER_MODE",
            f"answer_mode 只接受 {' 或 '.join(_ANSWER_MODES)}",
            recoverable=False,
        )
    return value


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


def _build_registry(store: object, *, allow_web: bool = True) -> ToolRegistry:
    """构建工具注册中心 — 参照 Pi 的扩展工具动态注册。

    `allow_web=False` 时**整组**摘掉 `search_web` / `fetch_url`（不是只摘搜索）——
    两者是同一件事（「这台机器能不能联外网」），只摘一个会造出「能抓网页但不能搜」
    这种用户无法理解、也没法配的半开状态。

    ⚠️ **必须与 prompt 层同源**：关掉时调用方要同时传 `web_search=False` 给
    `prompt_loader.build`，否则 system prompt 里还写着「可使用 search_web…」而工具表
    里已经没有它 —— 模型会调用一个不存在的工具（轻则白写一段，重则服务端 400）。
    两个开关在 `chat_stream` 里由**同一个**布尔量驱动（见该函数）。
    """
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
    if allow_web:
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


def _record_tool_start(tool_calls: list[dict], event: dict) -> None:
    """`tool_start` → 落盘条目（原地追加）。

    ⚠️ 这是**落盘白名单**：只存这四个字段。`args` 刻意不存（含用户输入的搜索词 / URL，
    且前端已由 `display` 表达）。加字段时别忘了前端 `chatStore.ts::toBackendMsg`
    那份成对白名单，以及 `core/types.py::ChatMessage.to_dict()`（整体透传、不校验内部）。

    `summary` / `sources` 此刻还没有（工具没跑完），由 `_record_tool_result` 回填 ——
    此前这里**只存了 `tool` 与 `display`**，于是「流式完成后落盘的气泡没有摘要」，
    而重新打开会话从 `toBackendMsg` 拿回来的却带着摘要 ⇒ 同一个东西两种状态。

    与 `_append_refs` 同因抽成模块级：调用点在嵌套的 async generator 里，测试够不到。
    """
    tool_calls.append({
        "tool": event["tool"],
        "display": event.get("display", ""),
        "summary": "",
        "sources": [],
    })


def _record_tool_result(tool_calls: list[dict], event: dict) -> None:
    """`tool_result` → 回填**同名且尚未填过的第一条**（原地修改）。

    agent.py 一轮内先发全部 `tool_start`、再逐个 `tool_result`（`for tc in
    msg.tool_calls` 里 await，严格顺序、非并发），故配对稳定；同一轮里同名工具被调用
    两次也能各归各位（第一条填完就轮到第二条）。

    ⚠️ **配对判据是「`summary` 尚为空」—— 它依赖另一个模块的不变量**：
    `agent.py::_tool_summary` 必须**恒返回非空串**（它的每个分支都 return 了文案，最差
    也是 `"搜索完成"`）。若哪天给它加一条「未知工具返回 `""`」的路径，这里的
    `not tc["summary"]` 就会把**同一个气泡**认成「还没填过」，于是后续的同名
    `tool_result` 反复覆盖第一条、后面的气泡永远空着 —— **零报错**。
    跨模块隐式契约，改 `_tool_summary` 的返回可能时要回来看这里。
    """
    for tc in tool_calls:
        if tc["tool"] == event["tool"] and not tc["summary"]:
            tc["summary"] = event.get("summary", "")
            tc["sources"] = event.get("sources", [])
            return


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
    system = prompt_loader.build(doc_text, answer_mode=_answer_mode(req.answer_mode))
    history = trim_history(req.history or [], budget, history_limit=cc.get("history_limit", 0))

    agent = AgentRunner(llm, max_rounds=3)
    # ⚠️ **非流式端点没有联网开关**，两处都用默认值（工具表含 search_web/fetch_url、
    # system prompt 是联网层）。这是**已知的不对称**，不是漏改 —— 前端 `chatStore`
    # 只导入 `streamChat`，`api/chat.ts::chat()` 与本端点目前**都没有消费者**。
    # 但记一笔：将来 SDK / 脚本直接调它时，「关掉联网开关后 AI 不能上网」这句承诺
    # 在它上面**不成立**。真要用起来，得照 `chat_stream` 的做法把 `allow_web` 一路
    # 传给这两处（那个局部变量驱动的两处必须同时改，见 `web_search` 查询参数的注释）。
    # ⚠️ 「回答依据」是例外：它随 `ChatRequest.answer_mode` 一路传给了 `build`，
    # 故「严格 / 发散」这条承诺在本端点上**成立**（联网那条仍然不成立）。
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
    follow_up: int = Query(0),
    web_search: int = Query(1),
    answer_mode: str = Query("strict"),
    store=Depends(get_store),
    config=Depends(get_config),
):
    """SSE 流式对话 — 实时推送回答 + 工具调用进度 + 自动持久化

    ⚠️ **入口先过来源守卫**（`ensure_trusted_request`）：这是个 GET 且**有副作用**
    （计费 + 会话写入），跨站 `<img src>` 就能触发。CORS 只挡读响应、不挡发出。

    `follow_up=1` = 用户开启了「追问建议」：回答结束后若模型没自己写可用的 `follow-up`
    块，由后端**补调一次** LLM 并推 `followup` 事件（见 `agent/followup.py`）。

    `web_search=0` = 用户关掉了「联网」：整组摘掉 `search_web` / `fetch_url`，并把
    system prompt 的联网层换成离线声明。**默认 1 = 改造前的现状**（联网一直可用）。
    ⚠️ 这个默认值与产物侧 `GenerateRequest.web_search`（默认 `False`）**方向相反且都是对的**
    —— 那边是**新增能力**（默认关 = 零回归），这边是**给已有能力加开关**（默认开 = 零回归）。

    `answer_mode`：`strict`（默认，只用知识库）/ `open`（知识库优先，可补充）/
    `free`（自由对话，不围绕知识库）。**默认 `strict` = 改造前的行为**（原 `rules.md` 就是
    「无来源不说话」），非法值显式报错、不静默退回 `strict`（见 `_answer_mode`）。
    """

    ensure_trusted_request(request)
    mode = _answer_mode(answer_mode)

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
    # ⚠️ `allow_web` 驱动**两处**：工具表（这里）与 system prompt 的联网层（下方
    #    `prompt_loader.build`）。拆成两个变量就是同源漂移的入口。
    allow_web = bool(web_search)
    agent = AgentRunner(llm, max_rounds=3)
    _build_registry(store, allow_web=allow_web).apply_to(agent)

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
    system = prompt_loader.build(system_text, web_search=allow_web, answer_mode=mode)
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
        # 追问补调的准入状态（判据见下方 `attempted`）
        saw_error = False
        cancelled = False
        async with disconnect_watcher(request, cancel_event):
            try:
                if compressed_count:
                    yield f"data: {json.dumps({'type': 'compressed', 'count': compressed_count}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'meta', 'conversation_id': conversation_id, 'context_window': window, 'budget': budget, 'price_in': price_in, 'price_out': price_out}, ensure_ascii=False)}\n\n"

                async for event in agent.stream_run(system, question, history, cancel_event=cancel_event):
                    if event["type"] == "text":
                        full_answer.append(event["text"])
                    elif event["type"] == "tool_start":
                        _record_tool_start(tool_calls, event)
                    elif event["type"] == "tool_result":
                        _record_tool_result(tool_calls, event)
                        _append_refs(refs, seen_ref_ids, event.get("refs"))
                    elif event["type"] == "thinking":
                        thinking_parts.append(event.get("text", ""))
                    elif event["type"] == "error":
                        saw_error = True
                    elif event["type"] == "done":
                        # `done` 有四处发出（agent.py：取消 / 错误 / CancelledError / 正常走完），
                        # 只有「正常走完且未取消」才允许补调追问。
                        cancelled = bool(event.get("cancelled"))
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

                answer = "".join(full_answer)

                # ── 追问补调（仅用户开启时）──
                # 准入判据在 `followup.should_attempt` 里（抽出去是为了让测试直调真实实现，
                # 而非在测试里复刻这五个条件）。
                attempted = should_attempt(bool(follow_up), saw_error=saw_error,
                                           cancelled=cancelled, answer=answer)
                follow_ups: list[str] | None = None
                if attempted:
                    follow_ups = await generate_followups(llm, question, answer, cancel_event=cancel_event)

                # ⚠️ **先落库、再推事件**（顺序不可颠倒）：反过来会让「写库失败」变成
                # 「前端有卡、刷新后没了」这种自相矛盾的状态；先落库则失败时两边都没有。
                sessions.append_assistant_message(
                    conversation_id,
                    answer,
                    doc_refs=refs or None,
                    tool_calls=tool_calls or None,
                    thinking="".join(thinking_parts) or None,
                    follow_ups=follow_ups,
                )

                if attempted:
                    # ⚠️ 推在 `done` **之后**（此处）：`parseSSEStream` 读到流关闭才 break，
                    # 前端收得到；而 `done` 一到输入框就已可用，用户不必等这几秒。
                    # ⚠️ 失败也要推（`questions: []` + `error`）——「开了开关却什么都没有」
                    # 必须看得出是失败，不是静默。
                    # ⚠️ 载荷形状走 `followup_payload`（与「换一批」端点**共用一份**）：
                    # 两个消费点渲染的是同一张卡，两处各拼一遍 dict = 失败文案会分叉。
                    yield f"data: {json.dumps(followup_payload(follow_ups), ensure_ascii=False)}\n\n"

                # ⚠️ **`blocks` 是本轮唯一不靠肉眼判断的模型行为信号**：`limo-block` 卡片组 /
                # 追问建议是**模型可选**的输出，遵循率与滥用率都只能在真实流量里看。
                # 没有这一项，「模型到底用没用」就只能翻聊天记录猜。
                # ⚠️ 用 `parse_blocks` 计数而不是数子串：正文里讨论本协议时也会出现
                # `limo-block` 这个词，那不算用了它。
                blocks = [s for s in parse_blocks(answer) if s["kind"] == "block"]
                log.info("Chat SSE 完成", conversation_id=conversation_id,
                         answer_len=len(answer),
                         blocks=len(blocks),
                         block_types=",".join(sorted({s["type"] for s in blocks})) or "-",
                         followups=len(follow_ups or []),
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


class FollowUpRequest(BaseModel):
    """「换一批」的入参 —— **无状态**：只带生成所需的原文，不带会话 / 消息标识。"""

    question: str = ""
    answer: str
    model_id: int | None = None


@router.post("/chat/followups")
async def refresh_followups(req: FollowUpRequest, config=Depends(get_config)) -> dict[str, object]:
    """为一条已有回答**重新生成**一批追问建议（前端「换一批」按钮）。

    ⚠️ **刻意做成无状态**（入参就是生成所需的原文，端点本身不碰 `SessionManager`）。
    按消息定位需要的标识在两处都不成立：① **客户端与服务端的消息 id 不同源** ——
    前端 `ChatMsg.id` 由 `nextMsgId()` 生成，服务端 `SessionManager.append_*` 生成的是
    `msg_…`，一次会话进行中两者**必然不同**；② 改用**下标**定位则多一层「前端数组与
    服务端数组是否同步」的隐含前提，而 `editMessage` / `deleteMessage` / `regenerate`
    三个动作都在改它（那正是这层前提会静默失效的地方）。
    代价是**落库由前端承担**（`chatStore.refreshFollowUps` 拿到新一批后调
    `rebuildConversationMessages`，与上述三个写动作同款）—— 没有「刷新后变回旧的一批」
    这种静默不一致。

    ⚠️ 与生成结束时的补调**共用同一份编排**：`generate_followups`（准入、超时、抽项）
    + `followup_payload`（形状与失败文案）。另写一份的话，两条路径产出的同一张卡会在
    「什么算失败、失败说什么」上分叉。

    ⚠️ 失败**不吞**（与 SSE 侧「失败也推事件」同一条纪律，但形态相反）：这里没有
    「已推送的流」可挂错误，且入参非法属**调用方错误**（空 answer 前端根本不会发）。
    真正生成不出问题（模型超时 / 抽不出项）走**成功响应 + `error` 字段**，
    由前端决定怎么显示 —— 那是可预期的业务结果，不是请求失败。
    """
    if not req.answer.strip():
        raise AppError("CHAT_FOLLOWUP_NO_ANSWER", "没有可用于生成追问的回答内容", recoverable=False)

    model_id = req.model_id or config.get("scene_models", {}).get("chat")
    llm = LLMService(config, model_id=model_id)
    if not llm.api_key:
        raise AppError("CHAT_NO_API_KEY", "LLM API Key 未配置，请在设置中填写", recoverable=False)

    try:
        # ⚠️ 不传 `cancel_event`：本请求的生命周期就是这次调用，客户端断开由 ASGI 层
        # 取消任务承担；SSE 侧那个 `cancel_event` 属于**对话流**，这里根本没有它。
        questions = await generate_followups(llm, req.question, req.answer)
        return followup_payload(questions)
    finally:
        # 本端点**自己新建**了 llm，故必须关（与 `_run_followup_llm` 那条
        # 「复用调用方的实例、绝不 aclose」的纪律不冲突：那条说的是别关**别人的**）。
        try:
            await llm.aclose()
        except Exception:
            pass
