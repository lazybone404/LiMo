"""
AgentRunner — 工具调用循环，内部使用 LLMService

与旧 Agent (agent/loop.py) 的关键差异：
1. LLMService 替代裸 OpenAI 客户端 → 统一的流式/取消/错误处理
2. stream_run() 支持流式输出 + 工具调用进度事件
3. register_tool 接口完全兼容——chat 路由代码不用改
"""

import asyncio
import json
import logging
from typing import Any, Callable, AsyncGenerator
from urllib.parse import urlparse

from app.llm.errors import CancelledError

logger = logging.getLogger(__name__)

ToolFn = Callable[..., str]


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
        if isinstance(data, list):
            return f"找到 {len(data)} 条相关结果"
        if isinstance(data, dict) and data.get("results"):
            return f"找到 {len(data['results'])} 条相关结果"
        return "搜索完成"

    if tool_name == "fetch_url":
        text = result if isinstance(result, str) else str(result)
        return f"已读取网页，约 {len(text)} 字"

    if tool_name == "search_docs":
        if isinstance(data, dict):
            return f"找到 {data.get('found', 0)} 个相关文档"
        return "搜索完成"

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
    ) -> AsyncGenerator[dict, None]:
        """流式多轮工具调用循环。

        工具判定回合用非流式（需完整 tool_calls 解析），
        最终回答用流式实时输出。

        产出事件：
          {"type": "text", "text": "..."}
          {"type": "tool_start", "tool": "x", "args": {...}}
          {"type": "tool_result", "tool": "x", "result": "..."}
          {"type": "done"}
          {"type": "error", "message": "..."}
        """
        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history[-20:])
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
                response = await self.llm.client.chat.completions.create(
                    model=self.llm.model,
                    messages=messages,
                    tools=self._tool_defs,
                    temperature=0.7,
                )
            except Exception as e:
                yield {"type": "error", "message": f"LLM 调用失败: {str(e)[:200]}"}
                return

            msg = response.choices[0].message

            # 无工具调用 → 直接返回（已有答案，不需要额外流式调用）
            if not msg.tool_calls:
                if msg.content:
                    yield {"type": "text", "text": msg.content}
                yield {"type": "done"}
                return

            # 有工具调用 → 执行
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

                logger.info("Tool [%d/%d]: %s(%s)",
                           round_i + 1, self.max_rounds, fn_name, fn_args)

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
                    logger.warning("Tool %s 执行失败: %s", fn_name, str(e)[:120])

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

                yield {
                    "type": "tool_result",
                    "tool": fn_name,
                    "summary": _tool_summary(fn_name, result),
                }

        # 达到最大轮数 → 带完整消息历史的流式最终回答
        yield {"type": "agent_status", "message": "正在整理回答..."}
        messages.append({
            "role": "user",
            "content": "请基于以上对话和工具调用结果给出最终回答（使用中文）。",
        })
        async for event in self._stream_response(messages, cancel_event):
            yield event

    async def _stream_response(
        self, messages: list[dict], cancel_event: asyncio.Event | None,
    ) -> AsyncGenerator[dict, None]:
        """用完整消息历史流式输出最终回答"""
        try:
            stream = await self.llm.client.chat.completions.create(
                model=self.llm.model,
                messages=messages,
                temperature=0.7,
                stream=True,
            )
            async for chunk in stream:
                if cancel_event and cancel_event.is_set():
                    raise CancelledError()
                delta = chunk.choices[0].delta
                if delta.content:
                    yield {"type": "text", "text": delta.content}
        except CancelledError:
            yield {"type": "done", "cancelled": True}
            return
        except Exception as e:
            yield {"type": "error", "message": str(e)[:200]}
            return
        yield {"type": "done"}
