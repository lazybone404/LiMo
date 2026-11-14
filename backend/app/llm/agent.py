"""
AgentRunner — 工具调用循环，内部使用 LLMService

与旧 Agent (agent/loop.py) 的关键差异：
1. LLMService 替代裸 OpenAI 客户端 → 统一的流式/取消/错误处理
2. stream_run() 支持流式输出 + 工具调用进度事件
3. register_tool 接口完全兼容——chat 路由代码不用改
"""

import asyncio
import json
from typing import Any, Callable, AsyncGenerator
from urllib.parse import urlparse

import openai

from app.core import logging as log
from app.llm.errors import CancelledError

ToolFn = Callable[..., str]


async def _create_with_retry(coro_factory: Callable[[], Any], retries: int = 2) -> Any:
    """create 阶段（请求发起前）429/5xx 退避重试（C5）。

    边界：仅 create 前重试——stream 流式中途失败不重试（已输出部分重试会重复输出），
    直接由调用方转 error 事件，前端可手动重新生成。
    仅重试 RateLimitError(429) / InternalServerError(5xx) / APITimeoutError / APIConnectionError；
    鉴权/参数等其他错误直接抛。
    """
    for attempt in range(retries + 1):
        try:
            return await coro_factory()
        except (openai.RateLimitError, openai.InternalServerError,
                openai.APITimeoutError, openai.APIConnectionError) as e:
            if attempt >= retries:
                raise
            log.warning("LLM create 重试", attempt=attempt + 1, error=str(e)[:100])
            await asyncio.sleep(1 + attempt * 2)  # 退避：1s / 3s


def _tool_display(tool_name: str, args: dict) -> str:
    """生成工具调用的安全展示文案（不暴露本地路径/完整 URL）"""
    if tool_name == "search_web":
        q = args.get("query", "")
        return f"正在搜索网络: {q}" if q else "正在搜索网络..."
    if tool_name == "fetch_url":
        url = str(args.get("url", ""))
        try:
            domain = urlparse(url).netloc or url[:60]
        except Exception:
            domain = url[:60] if url else ""
        return f"正在读取: {domain}" if domain else "正在读取网页..."
    if tool_name == "search_docs":
        q = args.get("query", "")
        return f"正在搜索知识库: {q}" if q else "正在搜索知识库..."
    if tool_name == "lookup_doc":
        return "正在读取文档..."
    if tool_name == "list_docs":
        return "正在列出可用文档..."
    return f"正在执行: {tool_name}"


def _tool_summary(tool_name: str, result: str) -> str:
    """生成工具执行结果的安全摘要（C 端不暴露完整内容）"""
    try:
        data = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        data = result

    if tool_name == "search_web":
        # ⚠️ **只认 dict 形状**。三家 provider（tavily / searxng / ddg）在
        # `agent/tools.py` 里的 return 全是 `{"results": [...]}`，此处曾有一个
        # `isinstance(data, list)` 分支 —— 那是**不可达的死代码**。
        # 删掉它，而**不是**反过来给 `_extract_sources` 补 list 兼容：形状判据要收在
        # **同一侧**，两处对「什么是合法返回」给出不同答案就是模式 #54 的入口。
        if isinstance(data, dict) and data.get("results"):
            return f"找到 {len(data['results'])} 条相关结果"
        return "搜索完成"

    if tool_name == "fetch_url":
        text = result if isinstance(result, str) else str(result)
        return f"已读取网页，约 {len(text)} 字"

    if tool_name == "search_docs":
        if not isinstance(data, dict):
            return "搜索完成"
        found = data.get("found", 0)
        # ⚠️ **`hasMore` 必须读**：`found` 是「返回了几条」（上限 `SEARCH_DOCS_LIMIT`），
        # 不是「命中了多少篇」。不读它时，一个命中 200 篇的查询会显示成「找到 5 个
        # 相关文档」—— 那不是省略，是**说假话**：用户会以为库里就这 5 篇。
        if data.get("hasMore"):
            return f"找到 {found} 个以上相关文档"
        # 降级时结果来自标题子串过滤，说「找到」会让用户以为检索跑过了。
        if data.get("degraded"):
            return f"检索不可用，按标题找到 {found} 个文档"
        return f"找到 {found} 个相关文档"

    if tool_name == "lookup_doc":
        if isinstance(data, dict):
            title = data.get("title", "")
            length = data.get("totalLength", len(result))
            if title:
                return f"已读取「{title}」，约 {length} 字"
            return f"已读取文档，约 {length} 字"
        return "已读取文档"

    if tool_name == "list_docs":
        if isinstance(data, list):
            return f"共 {len(data)} 个可用文档"
        return "已列出可用文档"

    return "工具执行完成"


def _safe_http_url(url: object) -> str:
    """只放行 http/https 绝对 URL，不合规返回空串。

    搜索结果里的 URL **来自公网**（Tavily / SearXNG / DDG 返回什么就是什么），不可信 ——
    直接拼进 `<a href>` 就多了一条 `javascript:` / `data:` 通道。这里是**主防线**：
    不合规的 URL 在抽取阶段就被丢掉；前端渲染前还会做一次同条件校验（纵深防御）。
    """
    u = str(url or "").strip()
    if not u or len(u) > 2000:
        return ""
    # ⚠️ 夹带空白的串必须拒：`urlparse("https://a b")` 的 netloc 是 `"a b"`（非空），
    # 只判 scheme/netloc 会让它通过 —— 而那种串进 `href` 有可能被浏览器截断解析。
    # 前端 `chat-sources.ts::isSafeSourceUrl` 的正则本就拒它（`[^\s]+$`），
    # 这里补齐才真的是「同条件校验」。
    if any(c.isspace() for c in u):
        return ""
    try:
        parts = urlparse(u)
    except ValueError:
        return ""
    return u if parts.scheme in ("http", "https") and parts.netloc else ""


def _extract_sources(tool_name: str, result: str, args: dict | None = None) -> list[dict]:
    """从工具结果中提取**外部来源**（title/url），供前端展示「搜了什么、来源在哪」。

    ⚠️ 与 `_extract_refs` 是**两件事**，不合并：`refs` 是**库内文档**（docId/title，
    点了跳预览区，不离开应用），这里是**公网来源**（title/url，点了开浏览器）。形状不同、
    消费方不同、跳转语义不同 —— 混成一个列表会让前端分不出该跳哪。

    ⚠️ 「C 端不暴露完整工具结果」这条约束**不管来源列表**：它管的是 fetch_url 的 4000 字
    正文、lookup_doc 的 2000 字全文。来源的 `title` + `url` 与 `refs` 里已有的
    `{"docId", "title"}` 是同一种粒度，此处只是把它补到 search_web / fetch_url 上。
    """
    args = args or {}

    if tool_name == "search_web":
        try:
            data = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return []
        results = (data or {}).get("results", []) if isinstance(data, dict) else []
        out: list[dict] = []
        for r in results:
            if not isinstance(r, dict):
                continue
            url = _safe_http_url(r.get("url"))
            if not url:
                continue
            out.append({"title": str(r.get("title", ""))[:200], "url": url})
            # 上限 5 条（与 `_extract_refs` 同款）。⚠️ 这是**展示**上限，与气泡文案里
            # 的「找到 N 条」**刻意允许不等** —— 那个 N 是搜索结果总数（`_tool_summary`
            # 报的是工具返回了什么），这个只决定展开列表画多长。强行对齐就得二选一：
            # 要么砍掉气泡里的真实数字，要么把来源列表撑成无上限（UI 没那个承载）。
            if len(out) >= 5:
                break
        return out

    if tool_name == "fetch_url":
        # 失败返回 `{"error": ...}`，正文没拿到 —— 也就没有可展示的来源
        try:
            data = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            data = None
        if isinstance(data, dict) and data.get("error"):
            return []
        url = _safe_http_url(args.get("url"))
        return [{"title": urlparse(url).netloc, "url": url}] if url else []

    return []


def _extract_refs(tool_name: str, result: str) -> list[dict]:
    """从工具结果中提取引用的文档（docId/title），供前端渲染 assistant 引用块"""
    try:
        data = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return []
    if tool_name == "search_docs":
        results = (data or {}).get("results", []) if isinstance(data, dict) else []
        return [{"docId": r["id"], "title": r.get("title", "")} for r in results if r.get("id")][:5]
    if tool_name == "lookup_doc":
        if isinstance(data, dict) and data.get("docId"):
            return [{"docId": data["docId"], "title": data.get("title", "")}]
        return []
    if tool_name == "list_docs":
        return [{"docId": r["id"], "title": r.get("title", "")}
                for r in data if isinstance(r, dict) and r.get("id")][:5]
    return []


# ── 深度联网：内置服务端工具（DeepSeek，2026-11-14）──
#
# ⚠️ 这一族的四个名字是**官方协议字面量**，不是 LiMo 起的工具名：请求里带
# `{"type": "deepseek_web_search"}` 这类字段，检索由**服务端**完成，模型不会产出
# `tool_calls`。所以它们不走 `register_tool` / `_tool_fns` 那套本地工具循环 ——
# 混进那套的唯一结果是服务端 400（官方明确「不能与其它工具混用」）。
SERVER_SEARCH_TOOL = "deepseek_web_search"
SERVER_READ_TOOL = "deepseek_read_url"
SERVER_TOOLS: tuple[str, ...] = (SERVER_SEARCH_TOOL, SERVER_READ_TOOL)

#: `server_tool_calls` 合成来源列表的展示上限（与 `_extract_sources` 的 5 条同口径）。
_MAX_SERVER_SOURCES = 5


def _extra(obj: object, key: str) -> object:
    """读 SDK **未声明**的扩展字段（`server_tool_calls` / `annotations` / `title`）。

    ⚠️ 一律经 `model_extra`，**不写** `getattr(delta, key)` 后假定它存在：这几个字段
    不在 OpenAI SDK 的响应模型里，`getattr` 的默认值会让「字段没来」与「字段来了但是
    空」变得无法区分 —— 而它们的语义差别很大（前者是这家端点没实现，后者是本轮没搜）。
    """
    extra = getattr(obj, "model_extra", None) or {}
    return extra.get(key)


def _server_tool_status(event: object) -> str:
    """`server_tool_event` → 一句进度文案。只分两种，不做细粒度 UI。

    载荷形状官方没给字段表，故按 JSON 里出没出现 `read_url` 粗分「检索 / 读网页」——
    分错的代价只是进度文案不够准，不分则用户在一分钟的等待里看到的是空白。
    """
    try:
        text = event if isinstance(event, str) else json.dumps(event, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(event)
    return "正在读取网页..." if SERVER_READ_TOOL in text else "正在联网检索..."


def _title_text(raw: object) -> str:
    """`delta.title` → 纯文本标题。端点可能给字符串，也可能给 `{"title": ...}`。"""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        return str(raw.get("title") or raw.get("content") or "").strip()
    return ""


def _server_tool_sources(server_tool_calls: list | None) -> list[dict]:
    """`server_tool_calls[].calls[].sources[]` → `[{title, url}]`（按 URL 去重、保序）。

    ⚠️ 形状不明，故逐层 `isinstance` 收窄：这串数据来自服务端，`calls` / `sources`
    任一缺失都不该让整轮回答炸掉 —— 来源列表是**附加信息**，正文才是主体。

    ⚠️ URL 仍过 `_safe_http_url`：这里的链接来自公网检索，与 `search_web` 的返回
    同级不可信。前端 `toChatSources` 会再过滤一遍（纵深防御），但主防线在这里。
    """
    out: list[dict] = []
    seen: set[str] = set()
    for item in server_tool_calls or []:
        if not isinstance(item, dict):
            continue
        for call in item.get("calls") or []:
            if not isinstance(call, dict):
                continue
            page = call.get("page")
            page_title = str(page.get("title") or "") if isinstance(page, dict) else ""
            for src in call.get("sources") or []:
                if not isinstance(src, dict):
                    continue
                url = _safe_http_url(src.get("url"))
                if not url or url in seen:
                    continue
                seen.add(url)
                title = str(src.get("title") or src.get("page") or page_title
                            or urlparse(url).netloc)[:200]
                out.append({"title": title, "url": url})
                if len(out) >= _MAX_SERVER_SOURCES:
                    return out
    return out


class AgentRunner:
    """通用 Agent 循环：LLM + Tools，支持流式输出。

    用法:
        llm = LLMService(config, model_id=chat_model_id)
        agent = AgentRunner(llm, max_rounds=3)
        agent.register_tool("search_docs", "搜索文档", {...}, fn)

        # 流式
        async for event in agent.stream_run(system, question):
            if event["type"] == "text":
                print(event["text"], end="")

        # 非流式（兼容旧接口）
        answer = await agent.run(system, question)
    """

    def __init__(self, llm: "LLMService", max_rounds: int = 3):
        self.llm = llm
        self.max_rounds = max_rounds
        self._tool_defs: list[dict] = []       # OpenAI tool 定义
        self._tool_fns: dict[str, ToolFn] = {}  # 实际函数

    def register_tool(self, name: str, description: str,
                      parameters: dict, fn: ToolFn):
        """注册工具——与旧 Agent 完全相同的接口"""
        self._tool_defs.append({
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters,
            },
        })
        self._tool_fns[name] = fn

    # ── 非流式（兼容旧接口）──

    async def run(
        self, system_prompt: str, user_message: str,
        history: list[dict] | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> str:
        """非流式——收集 stream_run 的全部输出。有错误时返回错误信息。"""
        parts: list[str] = []
        errors: list[str] = []
        async for event in self.stream_run(
            system_prompt, user_message, history, cancel_event,
        ):
            if event["type"] == "text":
                parts.append(event["text"])
            elif event["type"] == "error":
                errors.append(event["message"])
        if not parts and errors:
            return f"对话失败: {errors[0]}"
        return "".join(parts)

    # ── 流式 ──

    async def stream_run(
        self,
        system_prompt: str,
        user_message: str,
        history: list[dict] | None = None,
        cancel_event: asyncio.Event | None = None,
        *,
        server_tools: bool = False,
        with_title: bool = False,
        safety_check: bool = False,
    ) -> AsyncGenerator[dict, None]:
        """流式多轮工具调用循环。

        工具判定回合用非流式（需完整 tool_calls 解析），
        最终回答用流式实时输出。

        产出事件（完整协议见 docs/对话协议.md）：
          {"type": "agent_status", "message": "..."}
          {"type": "tool_start", "tool": "x", "args": {...}, "display": "..."}
          {"type": "tool_result", "tool": "x", "summary": "...",
           "refs": [{docId, title}], "sources": [{title, url}]}
          {"type": "thinking", "text": "..."}
          {"type": "text", "text": "..."}
          {"type": "done", "cancelled": bool}
          {"type": "error", "message": "..."}

        `server_tools=True` 走**另一条分支**（`_stream_with_server_tools`），上面这套
        本地工具循环整段不执行 —— 官方明确内置服务端工具不能与其它工具混用，
        而 `self._tool_defs` 为空时本函数会误判成「无工具 → 直接流式回答」。
        两个消费点（`routers/chat.py` 的流式与非流式）里只有流式会传它。
        """
        if server_tools:
            async for event in self._stream_with_server_tools(
                system_prompt, user_message, history, cancel_event,
                with_title=with_title, safety_check=safety_check,
            ):
                yield event
            return

        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        if history:
            # 已由 chat.py 预算引擎裁剪（token 预算），不再二次截断
            messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        for round_i in range(self.max_rounds):
            if cancel_event and cancel_event.is_set():
                yield {"type": "done", "cancelled": True}
                return

            # 无工具 → 直接流式回答
            if not self._tool_defs:
                async for event in self._stream_response(messages, cancel_event):
                    yield event
                return

            # 有工具 → 先用非流式判定是否需要工具调用
            yield {"type": "agent_status", "message": "正在分析问题..."}
            try:
                response = await _create_with_retry(
                    lambda: self.llm.client.chat.completions.create(
                        model=self.llm.model,
                        messages=messages,
                        tools=self._tool_defs,
                        temperature=0.7,
                    ))
            except Exception as e:
                yield {"type": "error", "message": f"LLM 调用失败: {str(e)[:200]}"}
                return

            msg = response.choices[0].message

            # 无工具调用 → 直接返回（已有答案，不需要额外流式调用）
            if not msg.tool_calls:
                log.info("Agent 直接回答（LLM 判定无需工具）",
                         round=round_i + 1, chars=len(msg.content or ""))
                if msg.content:
                    yield {"type": "text", "text": msg.content}
                yield {"type": "done"}
                return

            # 有工具调用 → 执行
            log.info("Agent 工具轮次", round=round_i + 1, tools=len(msg.tool_calls))
            messages.append(msg)
            # 收集所有工具调用的 display 和 args
            tool_displays = []
            for tc in msg.tool_calls:
                try:
                    tc_args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    tc_args = {}
                tool_displays.append({
                    "tool": tc.function.name,
                    "args": tc_args,
                    "display": _tool_display(tc.function.name, tc_args),
                })
            for td in tool_displays:
                yield {
                    "type": "tool_start",
                    "tool": td["tool"],
                    "args": td["args"],
                    "display": td["display"],
                }

            for tc in msg.tool_calls:
                fn_name = tc.function.name
                try:
                    fn_args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    fn_args = {}

                log.info("Tool 调用", round=round_i + 1, max_rounds=self.max_rounds,
                         tool=fn_name, args=fn_args)

                fn = self._tool_fns.get(fn_name)
                try:
                    if fn:
                        if asyncio.iscoroutinefunction(fn):
                            result = await fn(**fn_args)
                        else:
                            result = fn(**fn_args)
                    else:
                        result = json.dumps(
                            {"error": f"unknown tool: {fn_name}"}, ensure_ascii=False)
                except Exception as e:
                    result = json.dumps({"error": str(e)}, ensure_ascii=False)
                    log.warning("Tool 执行失败", tool=fn_name, error=str(e)[:120])

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

                yield {
                    "type": "tool_result",
                    "tool": fn_name,
                    "summary": _tool_summary(fn_name, result),
                    "refs": _extract_refs(fn_name, result),
                    "sources": _extract_sources(fn_name, result, fn_args),
                }

        # 达到最大轮数 → 带完整消息历史的流式最终回答
        log.warning("Agent 达到最大轮数，强制整理回答", max_rounds=self.max_rounds)
        yield {"type": "agent_status", "message": "正在整理回答..."}
        messages.append({
            "role": "user",
            "content": "请基于以上对话和工具调用结果给出最终回答（使用中文）。",
        })
        async for event in self._stream_response(messages, cancel_event):
            yield event

    async def _stream_with_server_tools(
        self,
        system_prompt: str,
        user_message: str,
        history: list[dict] | None,
        cancel_event: asyncio.Event | None,
        *,
        with_title: bool = False,
        safety_check: bool = False,
    ) -> AsyncGenerator[dict, None]:
        """深度联网：只带两个内置服务端工具，一次请求出完整回答。

        与本地工具循环的差别是**没有循环**：检索由服务端完成，模型不产出 `tool_calls`，
        故一次性开流读到底即可。四条容易被误当成「没实现」的约束：

        1. **不带 `tool_choice`**。官方写明它不能指定这两个工具，而设为 `none` 会连
           读链接一起关掉 —— 设什么都比不设差。
        2. **`title` 只在首轮带**（`with_title`），后续轮次带它等于每轮都让服务端改标题；
           而改不改由 `SessionManager.set_title_if_default` 二次裁决（用户手改过的不动）。
        3. **`finish_reason == "content_filter"` 必须发 `error`**：那种情况下正文为空是
           **正常形态**，但用户看到的必须是「被审核拦下」而不是「AI 什么都没说」。
        4. **`tool_start` / `tool_result` 照发**：前端的工具气泡、来源列表、落盘
           `tool_calls` 全靠这对事件，少一个就变成「回答有引用、界面上没有来源」。
           它们与本地工具同名同形，故 `_record_tool_result` / `toChatSources` 零改动。

        额外的 `server_context` 事件是**给后端自己**的（多轮连续性：把上一轮的
        `server_tool_calls` 原样带回，否则第二轮模型不记得读过哪些页面）。
        `routers/chat.py` 会截下它、不转发给前端 —— 它的载荷是原始协议结构，前端无消费方。
        """
        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        extra: dict[str, Any] = {}
        if with_title:
            extra["title"] = {"enabled": True}
        if safety_check:
            extra["safety_check"] = {"level": "high"}

        yield {"type": "tool_start", "tool": SERVER_SEARCH_TOOL, "args": {},
               "display": "正在深度联网..."}

        # 三个扩展字段都是「逐帧追加」：端点可能在多帧里分批给（尤其 sources）。
        title = ""
        server_tool_calls: list = []
        annotations: list = []
        last_status = ""
        filtered = False
        try:
            stream = await _create_with_retry(
                lambda: self.llm.client.chat.completions.create(
                    model=self.llm.model,
                    messages=messages,
                    tools=[{"type": t} for t in SERVER_TOOLS],
                    temperature=0.7,
                    stream=True,
                    # `title` / `safety_check` 不是 OpenAI 协议字段，只能走 extra_body
                    extra_body=extra or None,
                ))
            async for chunk in stream:
                if cancel_event and cancel_event.is_set():
                    raise CancelledError()
                choice = chunk.choices[0]
                delta = choice.delta
                if getattr(choice, "finish_reason", None) == "content_filter":
                    filtered = True
                # 进度事件官方写在 `choice` 上；两处都探一次只是因为它不在 SDK 模型里，
                # 放错一层就整条进度线静默消失（而它不影响正文，很难被发现）。
                event = _extra(choice, "server_tool_event") or _extra(delta, "server_tool_event")
                if event:
                    status = _server_tool_status(event)
                    if status != last_status:
                        last_status = status
                        yield {"type": "agent_status", "message": status}
                if getattr(delta, "reasoning_content", None):
                    yield {"type": "thinking", "text": delta.reasoning_content}
                if delta.content:
                    yield {"type": "text", "text": delta.content}
                for bucket, key in ((server_tool_calls, "server_tool_calls"),
                                    (annotations, "annotations")):
                    got = _extra(delta, key)
                    if got:
                        bucket.extend(got if isinstance(got, list) else [got])
                got_title = _title_text(_extra(delta, "title"))
                if got_title:
                    title = got_title
        except CancelledError:
            yield {"type": "tool_result", "tool": SERVER_SEARCH_TOOL,
                   "summary": "已停止", "refs": [], "sources": []}
            yield {"type": "done", "cancelled": True}
            return
        except Exception as e:
            # 工具气泡必须收尾，否则前端那个「正在深度联网」会一直转（同 `_stream_response`
            # 的 error 路径不同：那里没有未闭合的工具气泡）。
            yield {"type": "tool_result", "tool": SERVER_SEARCH_TOOL,
                   "summary": "深度联网失败", "refs": [], "sources": []}
            yield {"type": "error", "message": f"深度联网失败: {str(e)[:200]}"}
            return

        sources = _server_tool_sources(server_tool_calls)
        yield {"type": "tool_result", "tool": SERVER_SEARCH_TOOL,
               "summary": (f"深度联网完成（{len(sources)} 个来源）" if sources
                           else "深度联网完成，未返回来源"),
               "refs": [], "sources": sources}
        if server_tool_calls or annotations:
            yield {"type": "server_context", "server_tool_calls": server_tool_calls,
                   "annotations": annotations}
        if title:
            yield {"type": "title", "title": title}
        if filtered:
            yield {"type": "error", "message": "内容安全审核拒绝，已停止生成"}
            return
        yield {"type": "done"}

    async def _stream_response(
        self, messages: list[dict], cancel_event: asyncio.Event | None,
    ) -> AsyncGenerator[dict, None]:
        """用完整消息历史流式输出最终回答"""
        try:
            # create 阶段可重试；stream 中途失败不重试（下方 except 直接 error）
            stream = await _create_with_retry(
                lambda: self.llm.client.chat.completions.create(
                    model=self.llm.model,
                    messages=messages,
                    temperature=0.7,
                    stream=True,
                ))
            async for chunk in stream:
                if cancel_event and cancel_event.is_set():
                    raise CancelledError()
                delta = chunk.choices[0].delta
                # deepseek 系列思考链：reasoning_content 单独事件，前端折叠展示
                if getattr(delta, "reasoning_content", None):
                    yield {"type": "thinking", "text": delta.reasoning_content}
                if delta.content:
                    yield {"type": "text", "text": delta.content}
        except CancelledError:
            yield {"type": "done", "cancelled": True}
            return
        except Exception as e:
            yield {"type": "error", "message": str(e)[:200]}
            return
        yield {"type": "done"}
