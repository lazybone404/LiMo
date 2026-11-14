"""深度联网：DeepSeek 内置服务端工具（`llm/agent.py` 的该分支 + 三处成对落点）。

判据按「失效形态会不会被别的东西掩盖」来选：

1. **请求里只带两个内置工具**，且不带 `tool_choice` —— 官方明确不能混用其它工具，
   也不能用 `tool_choice` 指定它们；混进去的形态是服务端 400，不是本地报错。
2. **`title` 只在首轮带** —— 每轮都带等于每轮都让服务端改标题，而界面上只是
   「标题自己变了」。
3. **`content_filter` 必须发 `error`** —— 那种情况下正文为空是正常形态，
   不发就只能看到一条空白回答。
4. **来源按 URL 去重并过滤非 http(s)** —— 链接来自公网，与 `search_web` 同级的
   不可信输入。
5. **历史只在深度联网请求里带回扩展字段** —— 普通请求多带两个键就是给不认它们的
   端点发脏字段。
6. **`set_title_if_default` 不覆盖用户手改过的标题** —— 覆盖了没人看得出来。
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.agent.prompts import loader as prompt_loader
from app.agent.session import SessionManager
from app.core.deepseek_api import supports_deepseek_builtin
from app.llm.agent import (
    SERVER_READ_TOOL,
    SERVER_SEARCH_TOOL,
    AgentRunner,
    _server_tool_sources,
    _title_text,
)
from app.routers.chat import _history_messages


class _Delta:
    def __init__(self, content=None, reasoning=None, **extra):
        self.content = content
        self.reasoning_content = reasoning
        self.model_extra = extra


class _Choice:
    def __init__(self, delta, finish_reason=None, **extra):
        self.delta = delta
        self.finish_reason = finish_reason
        self.model_extra = extra


class _Chunk:
    def __init__(self, choice):
        self.choices = [choice]


class _FakeLLM:
    """只实现 `_stream_with_server_tools` 用到的两个属性 + 一个 create。"""

    def __init__(self, chunks, captured: dict | None = None):
        self.model = "deepseek-flash"
        self._chunks = chunks
        self._captured = captured if captured is not None else {}
        self.client = self

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    async def create(self, **kwargs):
        self._captured.update(kwargs)

        async def gen():
            for c in self._chunks:
                yield c

        return gen()


async def _collect(agent, **kwargs):
    return [e async for e in agent._stream_with_server_tools(
        "SYS", "问题", None, None, **kwargs)]


def _run(agent, **kwargs):
    return asyncio.run(_collect(agent, **kwargs))


# ── 请求形状 ──

def test_request_carries_only_the_two_builtin_tools():
    captured: dict = {}
    agent = AgentRunner(_FakeLLM([_Chunk(_Choice(_Delta("答"), finish_reason="stop"))],
                                 captured))
    _run(agent)

    assert [t["type"] for t in captured["tools"]] == [SERVER_SEARCH_TOOL, SERVER_READ_TOOL]
    assert "tool_choice" not in captured, "tool_choice 不能指定这两个工具，设为 none 还会连读链接一起关掉"
    assert captured["model"] == "deepseek-flash"


def test_title_only_on_first_turn():
    captured: dict = {}
    agent = AgentRunner(_FakeLLM([_Chunk(_Choice(_Delta("答")))], captured))
    _run(agent, with_title=True)
    assert captured["extra_body"]["title"] == {"enabled": True}

    captured.clear()
    agent = AgentRunner(_FakeLLM([_Chunk(_Choice(_Delta("答")))], captured))
    _run(agent, with_title=False)
    assert "title" not in (captured["extra_body"] or {})


def test_safety_check_is_opt_in():
    captured: dict = {}
    agent = AgentRunner(_FakeLLM([_Chunk(_Choice(_Delta("答")))], captured))
    _run(agent, safety_check=False)
    assert "safety_check" not in (captured["extra_body"] or {})

    captured.clear()
    agent = AgentRunner(_FakeLLM([_Chunk(_Choice(_Delta("答")))], captured))
    _run(agent, safety_check=True)
    assert captured["extra_body"]["safety_check"] == {"level": "high"}


# ── 事件流 ──

def test_tool_bubbles_are_paired():
    """`tool_start` 与 `tool_result` 必须成对，否则前端那个气泡一直转。"""
    agent = AgentRunner(_FakeLLM([_Chunk(_Choice(_Delta("答")))]))
    events = _run(agent)
    types = [e["type"] for e in events]

    assert types.count("tool_start") == 1
    assert types.count("tool_result") == 1
    assert types.index("tool_start") < types.index("tool_result")
    assert types[-1] == "done"


def test_content_filter_emits_error_not_empty_answer():
    agent = AgentRunner(_FakeLLM([_Chunk(_Choice(_Delta(None), finish_reason="content_filter"))]))
    events = _run(agent)

    errors = [e for e in events if e["type"] == "error"]
    assert errors and "内容安全" in errors[0]["message"]
    assert not [e for e in events if e["type"] == "text"]
    assert "done" not in [e["type"] for e in events], "被拦下不是正常完成"


def test_thinking_and_text_are_forwarded():
    agent = AgentRunner(_FakeLLM([
        _Chunk(_Choice(_Delta(reasoning="想一下"))),
        _Chunk(_Choice(_Delta("回答"))),
    ]))
    events = _run(agent)
    assert {"type": "thinking", "text": "想一下"} in events
    assert {"type": "text", "text": "回答"} in events


def test_sources_and_context_are_reported_at_the_end():
    """来源走 `tool_result.sources`（前端零改动），原始字段走 `server_context`（落盘用）。"""
    calls = [{
        "calls": [{
            "sources": [
                {"title": "a", "url": "https://a.example/x"},
                {"title": "a 重复", "url": "https://a.example/x"},
                {"title": "坏链", "url": "javascript:alert(1)"},
                {"url": "https://b.example/y"},
            ]
        }]
    }]
    agent = AgentRunner(_FakeLLM([
        _Chunk(_Choice(_Delta("答", server_tool_calls=calls, annotations=[{"n": 1}])))
    ]))
    events = _run(agent)

    result = [e for e in events if e["type"] == "tool_result"][0]
    assert result["sources"] == [
        {"title": "a", "url": "https://a.example/x"},
        {"title": "b.example", "url": "https://b.example/y"},
    ]
    ctx = [e for e in events if e["type"] == "server_context"][0]
    assert ctx["server_tool_calls"] == calls and ctx["annotations"] == [{"n": 1}]


def test_title_event_only_when_model_returns_one():
    agent = AgentRunner(_FakeLLM([
        _Chunk(_Choice(_Delta("答", title={"title": "短标题"})))
    ]))
    events = _run(agent)
    assert {"type": "title", "title": "短标题"} in events

    agent = AgentRunner(_FakeLLM([_Chunk(_Choice(_Delta("答")))]))
    assert "title" not in [e["type"] for e in _run(agent)]


def test_server_tool_event_becomes_status():
    agent = AgentRunner(_FakeLLM([
        _Chunk(_Choice(_Delta(None), server_tool_event={"tool": SERVER_SEARCH_TOOL,
                                                       "phase": "call_started"})),
        _Chunk(_Choice(_Delta(None), server_tool_event={"tool": SERVER_READ_TOOL,
                                                       "phase": "call_started"})),
    ]))
    statuses = [e["message"] for e in _run(agent) if e["type"] == "agent_status"]
    assert statuses == ["正在联网检索...", "正在读取网页..."], "同一条文案不重复推"


def test_cancel_mid_stream_keeps_bubbles_paired():
    agent = AgentRunner(_FakeLLM([_Chunk(_Choice(_Delta("答")))]))
    cancel = asyncio.Event()
    cancel.set()

    async def run():
        return [e async for e in agent._stream_with_server_tools(
            "SYS", "问题", None, cancel)]

    events = asyncio.run(run())
    assert events[-1] == {"type": "done", "cancelled": True}
    assert "tool_result" in [e["type"] for e in events]


# ── 纯函数 ──

def test_sources_tolerate_missing_layers():
    """形状不明：`calls` / `sources` 任一缺失都不该让整轮回答炸掉。"""
    assert _server_tool_sources(None) == []
    assert _server_tool_sources([{}, {"calls": None}, {"calls": [{}]}]) == []
    assert _server_tool_sources([{"calls": [{"sources": [{"url": "not-a-url"}]}]}]) == []


def test_title_text_accepts_string_and_dict():
    assert _title_text("  标题 ") == "标题"
    assert _title_text({"title": "标题"}) == "标题"
    assert _title_text(None) == ""


# ── 历史落点（成对改） ──

def test_history_carries_extension_fields_only_in_deep_web():
    raw = [
        {"role": "user", "content": "问"},
        {"role": "assistant", "content": "答",
         "server_tool_calls": [{"a": 1}], "annotations": [{"n": 1}]},
    ]
    plain = _history_messages(raw, False)
    assert plain == [{"role": "user", "content": "问"},
                     {"role": "assistant", "content": "答"}]

    deep = _history_messages(raw, True)
    assert deep[1]["server_tool_calls"] == [{"a": 1}]
    assert deep[1]["annotations"] == [{"n": 1}]
    assert "server_tool_calls" not in deep[0], "user 消息没有这两个字段"


def test_chat_message_round_trips_extension_fields():
    """`to_dict()` 的白名单漏一行 = 写盘那一刻丢掉（与前端 `toBackendMsg` 成对）。"""
    from app.core.types import ChatMessage

    d = ChatMessage(id="m1", role="assistant", content="答",
                    server_tool_calls=[{"a": 1}], annotations=[{"n": 1}]).to_dict()
    assert d["server_tool_calls"] == [{"a": 1}]
    assert d["annotations"] == [{"n": 1}]

    empty = ChatMessage(id="m2", role="assistant", content="答").to_dict()
    assert "server_tool_calls" not in empty and "annotations" not in empty


# ── 标题 ──

class _Store:
    def __init__(self, title: str):
        self._conv = {"id": "c1", "title": title, "messages": []}

    def get_conversation(self, conv_id):
        return dict(self._conv) if conv_id == "c1" else None

    def save_conversation(self, conv):
        self._conv = conv


def test_title_overwrites_auto_generated_only():
    question = "这是一个很长的问题" * 10
    auto = question[:60] + "..."

    store = _Store(auto)
    assert SessionManager(store).set_title_if_default("c1", "模型给的标题", question) is True
    assert store._conv["title"] == "模型给的标题"

    # 用户手改过的：不动
    store = _Store("我自己起的名字")
    assert SessionManager(store).set_title_if_default("c1", "模型给的标题", question) is False
    assert store._conv["title"] == "我自己起的名字"


def test_title_ignores_empty_and_unknown_conversation():
    store = _Store("新对话")
    assert SessionManager(store).set_title_if_default("c1", "   ", "问") is False
    assert SessionManager(store).set_title_if_default("nope", "标题", "问") is False


# ── 判据与 prompt 层 ──

@pytest.mark.parametrize("provider,model,expected", [
    ("deepseek", "deepseek-flash", True),
    ("deepseek", "deepseek-v4-pro", False),
    ("openai", "deepseek-flash", False),
    ("", "", False),
])
def test_supports_deepseek_builtin(provider, model, expected):
    assert supports_deepseek_builtin(provider, model) is expected


def test_prompt_swaps_web_layer_in_deep_web_mode():
    deep = prompt_loader.build(web_search=True, deep_web=True)
    assert "深度联网" in deep
    assert "search_web" not in deep, "深度联网模式下没有本地工具，prompt 不能再提它"

    normal = prompt_loader.build(web_search=True, deep_web=False)
    assert "search_web" in normal and "深度联网" not in normal
