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
from app.agent.web_mode import resolve_web_mode
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


def test_服务端事件对外只用搜索与抓取两个名字():
    """⚠️ 厂商名（`deepseek_web_search` / `deepseek_read_url`）只许出现在**请求载荷**
    与日志里。它漏进事件的形态是「用户看到一个叫 deepseek_web_search 的工具」——
    而它就是「搜索」，同一个动作两套名字，下游每多一个消费方就多认一个厂商名。
    """
    agent = AgentRunner(_FakeLLM([_Chunk(_Choice(_Delta("答")))]))
    used = {e["tool"] for e in _run(agent) if "tool" in e}

    assert used == {"search_web"}
    assert SERVER_SEARCH_TOOL not in used


def test_开始读页时先收尾搜索气泡再开抓取气泡():
    """两个动作各一个气泡 —— 否则「正在读取网页」挂在一个叫「搜索」的气泡下面。

    ⚠️ 必须**一收一开**：只开不收的话，前端上一个气泡永远停在 running。
    """
    agent = AgentRunner(_FakeLLM([
        _Chunk(_Choice(_Delta(None), server_tool_event={"tool": SERVER_SEARCH_TOOL})),
        _Chunk(_Choice(_Delta(None), server_tool_event={"tool": SERVER_READ_TOOL})),
    ]))
    events = _run(agent)

    assert [e["tool"] for e in events if e["type"] == "tool_start"] == ["search_web", "fetch_url"]
    assert [(e["tool"], e["summary"]) for e in events if e["type"] == "tool_result"] == [
        ("search_web", "检索完成"), ("fetch_url", "读取完成，未返回来源")]


def test_两个气泡都能被落盘配对():
    """端到端：事件 → `_record_tool_*` → 两条都闭合。配对规则是「同名且摘要为空」，
    两个动作名不同，所以顺序对了就各归各位。
    """
    from app.routers.chat import _record_tool_result, _record_tool_start

    agent = AgentRunner(_FakeLLM([
        _Chunk(_Choice(_Delta(None), server_tool_event={"tool": SERVER_SEARCH_TOOL})),
        _Chunk(_Choice(_Delta(None), server_tool_event={"tool": SERVER_READ_TOOL})),
    ]))
    tcs: list[dict] = []
    for e in _run(agent):
        if e["type"] == "tool_start":
            _record_tool_start(tcs, e)
        elif e["type"] == "tool_result":
            _record_tool_result(tcs, e)

    assert [(t["tool"], t["summary"]) for t in tcs] == [
        ("search_web", "检索完成"), ("fetch_url", "读取完成，未返回来源")]


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


# ── 联网形态：三选一，一处判定（`agent/web_mode.py`，2026-11-24） ──
#
# 此前是 `(web_search, deep_web)` 两个布尔量，四处各自解释：「按钮亮不亮」在
# 前端、「能不能用」在路由的三目表达式、prompt 层在 `build` 里三选一、
# 历史字段在 `_history_messages` 再判一次。下面这几条钉住的是**只有一处**。

@pytest.mark.parametrize("allow,want,provider,model,key,local,net,server", [
    # 联网关：本地工具表还在（库里还能查），但联网两个被摘掉
    (False, False, "deepseek", "deepseek-flash", "off", True, False, False),
    # 联网关 + 要了深度：仍是 off，且要说「联网已关闭」
    (False, True, "deepseek", "deepseek-flash", "off", True, False, False),
    # 普通联网
    (True, False, "deepseek", "deepseek-flash", "local", True, True, False),
    # 要深度、模型支持 → server，且**不注册任何本地工具**
    (True, True, "deepseek", "deepseek-flash", "server", False, False, True),
    # 要深度、模型不支持 → 退回 local 并说明
    (True, True, "deepseek", "deepseek-v4-pro", "local", True, True, False),
])
def test_联网形态三选一(allow, want, provider, model, key, local, net, server):
    wm = resolve_web_mode(allow_web=allow, want_deep=want,
                          provider=provider, model=model)
    assert (wm.key, wm.local_tools, wm.net_tools, wm.server_tools) == (key, local, net, server)


def test_联网关优先于模型判据():
    """⚠️ 顺序判据：`web_search=0, deep_web=1` 时该说「联网已关闭」而不是
    「模型不支持深度联网」—— 前者是用户点一下就能解决的，后者不是。
    """
    wm = resolve_web_mode(allow_web=False, want_deep=True,
                          provider="deepseek", model="deepseek-flash")
    assert wm.key == "off"
    assert "联网已关闭" in wm.note


def test_模型不支持时退回本地并说明():
    wm = resolve_web_mode(allow_web=True, want_deep=True,
                          provider="openai", model="gpt-x")
    assert wm.key == "local"
    assert "不支持深度联网" in wm.note


def test_一切正常时不带说明():
    """`note` 是「与所选不一致」的信号。正常路径上它必须为空 ——
    否则前端每一轮都会弹一条无意义的 warning。
    """
    for wm in (resolve_web_mode(allow_web=True, want_deep=False,
                                provider="x", model="y"),
               resolve_web_mode(allow_web=True, want_deep=True,
                                provider="deepseek", model="deepseek-flash")):
        assert wm.note == ""


def test_接线_路由不再自己判_联网形态():
    """⚠️ 源码守卫：`routers/chat.py` 里出现 `supports_deepseek_builtin`
    或裸的 `deep_web_ok`，就说明判定又长回了第二处。

    失效形态是**静默**的：两个判据在绝大多数组合下给出同一个答案，
    只在「模型刚被换掉」这类少数组合上分叉 —— 那时按钮亮着但这一轮没联网。
    """
    import re
    from pathlib import Path
    body = (Path(__file__).resolve().parents[1] / "app" / "routers" / "chat.py").read_text(
        encoding="utf-8")
    # ⚠️ 先剥注释再判：注释里提到旧写法不算真在用（同 `test_chat_web_search` 的 `_chat_py`）
    body = re.sub(r"^\s*#.*$", "", body, flags=re.M)
    assert "supports_deepseek_builtin" not in body
    assert "deep_web_ok" not in body
    assert "resolve_web_mode(" in body
