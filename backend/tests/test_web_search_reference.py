"""产物侧联网检索 —— 生成前 LLM 从正文抽检索词 → 调搜索工具 → 结果汇总成**一份**
参考资料，走既有 `reference_texts` 通道注入笔记。

⚠️ **本文件是 pytest 形态（`def test_*`）**，不是脚本式。本仓有一批顶层裸断言的
脚本式测试，在 `bash scripts/check-all backend`（= `pytest tests/ -q`）下**收集 0 个、
静默空跑** —— 「断言全过」与「一个用例都没跑」在输出上都是绿的。

⚠️ **不发起真实网络请求**：`app.agent.tools.search_web` 被 monkeypatch 成假函数
（`web_search._search_all` 内部是**函数内** import ⇒ 从模块属性取，patch 模块属性有效）。
抽词那次 LLM 调用照 `test_artifact_followup.py` 的做法替换 `G.LLMService`。

⚠️ **本文件的核心纪律（同 `test_generate_gate.py` / `test_artifact_followup.py`）**：
每条「X 没发生」的断言都要配一条「该发生时确实发生了」的对照。只测「不勾选就不检索」
的实现，可以靠「一律不检索」全绿 —— 那正是最容易写出来的错版本。
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import app.agent.tools as TOOLS
import app.generation.web_search as W
import app.routers.generate as G
from app.core.concurrency import ExecutionGuard
from app.core.errors import AppError
from app.core.transcript_meta import parse_artifact_meta
from app.core.types import RawMetadata, RawTranscript, SourceType
from app.storage.store import Store

_TMP: list[str] = []


@pytest.fixture(scope="module", autouse=True)
def _cleanup():
    yield
    for d in _TMP:
        shutil.rmtree(d, ignore_errors=True)


#: 检索结果里的唯一标记 —— 断言「这份料真的进了 prompt」时用它，
#: 不用检索词本身（检索词同时出现在 stage 事件里，会混淆两类断言）。
SENTINEL = "SENTINEL-SNIPPET-9f2a"


# ── 假搜索工具 ───────────────────────────────────────────────────────────────

class FakeSearch:
    """`app.agent.tools.search_web` 的替身（**返回形状与真品一致**）。"""

    calls: list[tuple] = []
    #: 设为非 None 则原样返回（用来造「结构异常」）
    raw: str | None = None
    raises: bool = False
    #: 让每次调用返回不同条数，用来测去重与截断
    per_query: int = 2
    #: 每次调用的耗时 —— 用来行使 `_search_all` 的整体闸门与取消透传
    #: （照 `FakeLLMService.extract_delay` 的做法）。⚠️ 必须是可被取消的 `sleep`，
    #: 否则取消路径永远测不出「提前返回」。
    delay: float = 0.0

    @classmethod
    def reset(cls):
        cls.calls = []
        cls.raw = None
        cls.raises = False
        cls.per_query = 2
        cls.delay = 0.0


async def _fake_search_web(query: str, max_results: int = 5) -> str:
    FakeSearch.calls.append((query, max_results))
    if FakeSearch.delay:
        await asyncio.sleep(FakeSearch.delay)
    if FakeSearch.raises:
        raise RuntimeError("模拟搜索失败")
    if FakeSearch.raw is not None:
        return FakeSearch.raw
    results = [
        {"title": f"{query} 的第 {i} 条", "url": f"https://example.com/{query}/{i}",
         "snippet": f"{SENTINEL} 来自 {query} 的摘要 {i}"}
        for i in range(FakeSearch.per_query)
    ]
    return json.dumps({"results": results, "source": "tavily"}, ensure_ascii=False)


# ── 假 LLM ───────────────────────────────────────────────────────────────────

EXTRACT_OUTPUT = "- 检索词甲\n- 检索词乙"


class _Resp:
    def __init__(self, content: str):
        self.choices = [SimpleNamespace(message=SimpleNamespace(content=content))]


class _Completions:
    async def create(self, **kw):
        FakeLLMService.extract_calls.append(kw)
        if FakeLLMService.extract_raises:
            raise RuntimeError("模拟抽词失败")
        if FakeLLMService.extract_delay:
            await asyncio.sleep(FakeLLMService.extract_delay)
        return _Resp(FakeLLMService.extract_output)


class FakeLLMService:
    """最小替身：正文走 `stream`/`call`，抽检索词走 `client.chat.completions.create`。

    ⚠️ **`client` 是带守卫的 property**：`aclose()` 之后再取它会抛错 —— 让
    「在 `aclose` 之后还去检索」这类作用域错误**可观测**（否则假实例的关闭是空操作，
    用例会绿在错误的理由上）。
    """

    extract_calls: list[dict] = []
    extract_output: str = EXTRACT_OUTPUT
    extract_raises: bool = False
    extract_delay: float = 0.0
    stream_prompts: list[str] = []
    call_prompts: list[str] = []

    def __init__(self, config, model_id=None, request_timeout=None):
        self.provider = "dashscope"
        self.model = "qwen-max"
        self.api_key = "sk-fake-key-for-tests"
        self.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        #: 无 reasoning 能力位 ⇒ `thinking_extra()` 返回 None（不传 extra_body）
        self.capabilities = ["text"]
        self.supports_reasoning = False
        self.closed = False
        self._client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))

    @property
    def client(self):
        if self.closed:
            raise RuntimeError("连接池已关闭（检索在 aclose 之后发起）")
        return self._client

    async def call(self, prompt, **kw) -> str:
        FakeLLMService.call_prompts.append(prompt)
        return "# 生成的笔记\n\n这是模型产出的正文，足够长到不被当成空产物。"

    def stream(self, prompt, **kw):
        FakeLLMService.stream_prompts.append(prompt)
        return _FakeStream(["# 生成的笔记", "\n\n这是模型产出的正文。"])

    async def aclose(self) -> None:
        self.closed = True


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)

    async def aclose(self):
        self._chunks.clear()


# ── 测试 app（只挂 generate 路由，不触发主 app startup）──────────────────────

def _new_store() -> Store:
    d = tempfile.mkdtemp()
    _TMP.append(d)
    return Store(d)


_STORE = _new_store()
_CFG = {
    "llm": {"provider": "dashscope", "model": {"dashscope": "qwen-max"},
            "api_key": "sk-fake-key-for-tests",
            "base_url": {"dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1"}},
    "target_language": "zh-CN",
    "enabled_models": [{"id": 1, "provider_id": "dashscope", "model_name": "qwen-max",
                        "context_window": 1_000_000, "capabilities": ["text"]}],
    "data_dir": str(_STORE.root),
}

app = FastAPI()
app.include_router(G.router, prefix="/api")
app.state.store = _STORE
app.state.config = _CFG
app.state.guard = ExecutionGuard(cloud_max=5, local_max=2)


@app.exception_handler(AppError)
async def _app_error_handler(request, exc: AppError):
    return JSONResponse(status_code=200 if exc.recoverable else exc.status_code,
                        content=exc.to_dict())


client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_fakes(monkeypatch):
    """每条用例从「检索一切正常」的干净状态开始。

    ⚠️ **`G.LLMService` 的替换必须在这里做、不能在模块级** —— 它是模块级共享全局，
    多个测试文件都在 import 时各写一次 `G.LLMService = <自己的假类>`，pytest 在收集
    阶段 import 全部模块 ⇒ **最后 import 的那个赢**（症状是「单跑绿、全量红」，且红的
    形态是「拿到的东西不对」而非断言写错）。类属性同样要复位，否则上一轮的失败/延迟
    会渗到下一轮（用例之间产生顺序耦合）。
    """
    monkeypatch.setattr(G, "LLMService", FakeLLMService)
    monkeypatch.setattr(TOOLS, "search_web", _fake_search_web)
    FakeLLMService.extract_calls = []
    FakeLLMService.extract_output = EXTRACT_OUTPUT
    FakeLLMService.extract_raises = False
    FakeLLMService.extract_delay = 0.0
    FakeLLMService.stream_prompts = []
    FakeLLMService.call_prompts = []
    FakeSearch.reset()
    yield


def _mk_doc(tx_id: str, title: str, raw: str,
            stype: SourceType = SourceType.WEBPAGE) -> None:
    _STORE.save_transcript(RawTranscript(
        id=tx_id, title=title, source_type=stype, engine="test",
        raw_text=raw, content_hash="", metadata=RawMetadata(), tags=[]),
        skip_dedup=True)


def _sse(body: str) -> list[dict]:
    """解析 SSE 正文里的 data 事件（`: heartbeat` 注释行忽略，**保序**）。"""
    return [json.loads(ln[6:]) for ln in body.splitlines() if ln.startswith("data: ")]


def _stream(tx_id: str, query: str) -> list[dict]:
    with client.stream("GET", f"/api/generate/{tx_id}/stream?{query}") as r:
        body = "".join(r.iter_text())
    return _sse(body)


def _content(tx_id: str, mode: str) -> str:
    """取某篇某 mode 的**正文**（含自证头首行）。

    ⚠️ 不能用 `store.get_markdown(id)` —— 它收的是**条目 id**，不是 `(tx_id, mode)`。
    """
    for e in _STORE.get_markdowns_by_transcript(tx_id):
        if e.get("mode") == mode:
            return e.get("content") or ""
    return ""


def _web_param(tx_id: str, mode: str = "notes") -> dict | None:
    """自证头里的 `webSearch` 字段（没有该键时返回 None）。"""
    meta, _corrupt = parse_artifact_meta(_content(tx_id, mode))
    if not meta:
        return None
    return (meta.get("generated", {}).get("params") or {}).get("webSearch")


def _drive(llm, title: str, text: str, **kw):
    """把 `run_web_search` 生成器跑完，返回 `(事件列表, outcome)`。"""
    async def _go():
        events = []
        outcome = None
        async for kind, payload in W.run_web_search(llm, title, text, **kw):
            events.append((kind, payload))
            if kind == "result":
                outcome = payload
        return events, outcome
    return asyncio.run(_go())


class _StubLLM:
    """只提供给 `_extract_queries` / `_search_all` 用的最小 llm。"""

    model = "qwen-max"
    #: `thinking_extra()` 读的就是它（没有该能力位 ⇒ extra_body 不传）
    supports_reasoning = False

    def __init__(self, content: str = EXTRACT_OUTPUT, raises: bool = False):
        self._content = content
        self._raises = raises

    @property
    def client(self):
        content, raises = self._content, self._raises

        class _C:
            async def create(self, **kw):
                if raises:
                    raise RuntimeError("boom")
                return _Resp(content)

        return SimpleNamespace(chat=SimpleNamespace(completions=_C()))


# ═══════════════════════════════════════════════════════════════
# 1. 纯函数：抽词解析 / 组料 / 命名 / 自证头字段
# ═══════════════════════════════════════════════════════════════

def test_抽词解析_列表形态():
    got = W.parse_queries("- 稀土出口管制 2025\n- 中国稀土集团\n- 第三条不该要")
    assert got == ["稀土出口管制 2025", "中国稀土集团"], \
        f"只取前 MAX_QUERIES={W.MAX_QUERIES} 条；实际 {got}"


def test_抽词解析_乱序编号也认():
    got = W.parse_queries("1. 甲\n2) 乙")
    assert got == ["甲", "乙"], f"有序列表同样是列表项；实际 {got}"


def test_抽词解析_无列表时兜底且排除冒号结尾的引导语():
    """模型直接写两行短文本也认 —— 但「以下是检索词：」这类引导语必须排除。

    ⚠️ 对照：把 `endswith(("：", ":"))` 那条去掉 → 引导语会被当成第一个检索词，
    本条立刻变红（这正是它能证明的东西）。
    """
    got = W.parse_queries("以下是可用于搜索的检索词：\n稀土出口管制\n中国稀土集团")
    assert got == ["稀土出口管制", "中国稀土集团"], f"引导语不该被当成查询词；实际 {got}"


def test_抽词解析_超长截断():
    long = "长" * 100
    got = W.parse_queries(f"- {long}")
    assert len(got[0]) == W.MAX_QUERY_CHARS, \
        f"超长检索词必须截到 {W.MAX_QUERY_CHARS} 字（防模型把整句话当查询词）；实际 {len(got[0])}"


def test_抽词解析_空输出返回空列表():
    assert W.parse_queries("") == []
    assert W.parse_queries("   \n  \n") == []


def test_组料_编号与可信度声明与条目内容():
    results = [
        {"title": "标题一", "url": "https://a.example/1", "snippet": "摘要一"},
        {"title": "标题二", "url": "https://b.example/2", "snippet": "摘要二"},
    ]
    ref = W.compose_reference(["词甲", "词乙"], results)
    assert ref is not None
    assert ref["name"].startswith("联网检索："), f"名称要标明来源；实际 {ref['name']}"
    body = ref["content"]
    assert "未经核实" in body, "首行必须有可信度声明（它住在这份料自己身上，不在共享节首）"
    assert "1. **标题一**" in body and "2. **标题二**" in body, f"条目要编号；实际 {body}"
    assert "https://a.example/1" in body and "摘要一" in body


def test_组料_空条目返回None():
    """⚠️ 对照：`compose_reference` 若在 n==0 时返回 `{"content": ""}`，注入侧会多出
    一个空节（模型看到「参考资料」标题但下面什么都没有）——本条钉住它返回 None。"""
    assert W.compose_reference(["词"], []) is None
    assert W.compose_reference(["词"], [{"title": "", "snippet": "", "url": ""}]) is None


def test_组料_无标题时不漏条目():
    ref = W.compose_reference(["词"], [{"title": "", "snippet": "只有摘要", "url": ""}])
    assert ref is not None and "只有摘要" in ref["content"], \
        "标题为空但有摘要的条目不能整条丢掉"


def test_参考资料名_超长退化成通用名而非截半句():
    """⚠️ **不能依赖 `sanitize_reference_name` 的 80 字截断** —— 那会截在词中间，
    用户看到的是半句话。名称是我们自己组装的，超长就退化成完整但不带关键词的名字。
    """
    long = W._reference_name(["甲" * 30, "乙" * 30])
    assert long == "联网检索", f"超长应退化成通用名；实际 {long!r}"
    short = W._reference_name(["稀土", "出口"])
    assert "稀土" in short and "出口" in short


def test_自证头字段_零命中必须保留():
    """`hits: 0` 是「开了检索但一条都没搜到」与「压根没开检索」之间**唯一**的区分点。

    ⚠️ 对照：`compact_params` 用 `is not False` 判 ⇒ 数值 0 会被保留；若哪天有人把它
    改成 truthiness 判断，本条变红（而不是让两种状态在产物上变得不可区分）。
    """
    o = W.WebSearchOutcome(queries=["甲"], hits=0, error="联网检索没有返回结果")
    p = W.outcome_param(o)
    assert p["hits"] == 0, f"零命中要留下；实际 {p}"
    assert p["queries"] == ["甲"]
    assert p["error"] == "联网检索没有返回结果"

    ok = W.outcome_param(W.WebSearchOutcome(queries=["甲"], hits=3))
    assert "error" not in ok, "成功时不该有 error 键（缺 ≠ 空串）"


def test_抽词prompt含标题与正文():
    p = W.build_query_prompt("我的标题", "正文开头若干字")
    assert "我的标题" in p and "正文开头若干字" in p
    assert str(W.MAX_QUERIES) in p and str(W.MAX_QUERY_CHARS) in p, \
        "条数与长度上限必须写进 prompt（它是模型唯一的约束来源）"
    assert "{n}" not in p and "{title}" not in p, "占位符必须已被 format 掉"


# ═══════════════════════════════════════════════════════════════
# 2. 检索层：去重 / 截断 / 结构异常 / 契约
# ═══════════════════════════════════════════════════════════════

def test_检索_按url去重并截断():
    FakeSearch.per_query = 4  # 两个查询各 4 条 → 去重后应截到 MAX_RESULTS
    out = asyncio.run(W._search_all(["甲", "乙"]))
    assert len(out) == W.MAX_RESULTS, f"应截到 {W.MAX_RESULTS} 条；实际 {len(out)}"
    urls = [r["url"] for r in out]
    assert len(urls) == len(set(urls)), f"URL 必须去重；实际 {urls}"
    assert len(FakeSearch.calls) == 2, "两个检索词各调一次"


def test_检索_同一URL跨检索词只留一条():
    """两个检索词返回**同一批 URL** ⇒ 合并后条数不翻倍。

    ⚠️ 对照：去掉 `seen` 去重 → 本条变红（实际条数会变成 per_query × 2）。
    """
    FakeSearch.per_query = 2

    async def _same(query, max_results=5):
        # URL 与 query 无关 ⇒ 两个词返回完全相同的 URL
        return json.dumps({"results": [
            {"title": f"共同 {i}", "url": f"https://same.example/{i}", "snippet": "同"}
            for i in range(2)
        ]}, ensure_ascii=False)

    orig = TOOLS.search_web
    TOOLS.search_web = _same
    try:
        out = asyncio.run(W._search_all(["甲", "乙"]))
    finally:
        TOOLS.search_web = orig
    assert len(out) == 2, f"跨检索词的同 URL 必须去重；实际 {len(out)}"


def test_检索_结构异常返回空且不抛():
    """Agent 侧改了返回形状时，生成侧**不能**静默拿到空结果而不留痕。

    ⚠️ 这里断言的是「不抛且返回 []」；留痕（logger.warning）由 `caplog` 断言 ——
    只测返回值的话，「返回 [] 且什么都不记」的实现也能全绿，而那正是要防的静默。
    """
    FakeSearch.raw = json.dumps({"items": [{"title": "形状变了"}]})  # 键不是 results
    out = asyncio.run(W._search_all(["甲"]))
    assert out == [], f"结构异常应返回空；实际 {out}"

    FakeSearch.raw = "这不是 JSON"
    assert asyncio.run(W._search_all(["甲"])) == [], "非 JSON 同样返回空、不抛"


def test_检索_单路失败不影响另一路():
    """一路抛错、另一路正常 ⇒ 仍有结果（局部失败不吞掉整次检索）。

    ⚠️ 对照：把 `asyncio.gather` 换成串行且不 try/except → 第一路抛错会炸掉整次。
    """
    async def _half(query, max_results=5):
        if query == "甲":
            raise RuntimeError("甲路失败")
        return json.dumps({"results": [
            {"title": "乙的结果", "url": "https://ok.example/1", "snippet": SENTINEL}
        ]}, ensure_ascii=False)

    orig = TOOLS.search_web
    TOOLS.search_web = _half
    try:
        out = asyncio.run(W._search_all(["甲", "乙"]))
    finally:
        TOOLS.search_web = orig
    assert len(out) == 1 and out[0]["title"] == "乙的结果", f"应保住另一路；实际 {out}"


def test_检索_整体超时放弃等待(monkeypatch):
    """三级降级链「三轮都慢」时的整体闸门 —— 此前没有它，生成会一直吊在这里。

    ⚠️ **对照**：去掉 `asyncio.wait` 的 `timeout=SEARCH_TIMEOUT` → 这里会等满
    `delay` 才返回且有结果 ⇒ `out == []` 与 `elapsed` 两条断言同时变红。
    """
    # ⚠️ 把闸门压到 0.05s，别真等 60 秒（同 `test_抽词_超时返回空` 的做法）
    monkeypatch.setattr(W, "SEARCH_TIMEOUT", 0.05)
    FakeSearch.delay = 1.0
    t0 = time.monotonic()
    out = asyncio.run(W._search_all(["甲"]))
    elapsed = time.monotonic() - t0
    assert out == [], f"超时应放弃等待并返回空；实际 {out}"
    assert elapsed < 0.5, f"不该等满 delay；实际 {elapsed:.2f}s"


def test_检索_已取消时不发起搜索():
    """起跑前已取消 ⇒ 一个搜索请求都不发（不该再花时间/花钱）。

    ⚠️ **对照**：去掉开头那句 `cancel_event.is_set()` → `FakeSearch.calls` 非空。
    """
    ev = asyncio.Event()
    ev.set()
    out = asyncio.run(W._search_all(["甲", "乙"], ev))
    assert out == []
    assert FakeSearch.calls == [], f"取消后不该发起搜索；实际 {FakeSearch.calls}"


def test_检索_中途取消立即放弃():
    """检索**进行中**取消 ⇒ 不再等剩余结果。

    ⚠️ 此前这一层根本收不到 `cancel_event`，用户按了取消，界面仍要等满整条三级降级链。
    ⚠️ **对照**：把 `cancel_event` 从 `_search_all` 的参数里去掉 → 取消事件进不来，
    这里会等满 `delay`（1.0s）⇒ `elapsed < 0.5` 变红。
    """
    FakeSearch.delay = 1.0

    async def _go():
        ev = asyncio.Event()
        asyncio.get_running_loop().call_later(0.05, ev.set)
        t0 = time.monotonic()
        out = await W._search_all(["甲"], ev)
        return out, time.monotonic() - t0

    out, elapsed = asyncio.run(_go())
    assert out == [], f"取消后应放弃等待；实际 {out}"
    assert elapsed < 0.5, f"取消应立即可见；实际 {elapsed:.2f}s"


def test_搜索工具的返回结构是契约():
    """**源码哨兵**：`agent.tools.search_web` 的返回形状是 `{"results": [...]}`。

    ⚠️ 这条守卫**刻意写得弱**（只查键名字面量在场），因为它要防的不是「实现写错」而是
    「**另一侧改了形状而这一侧不知道**」—— `web_search._search_all` 按 `results` 取值，
    而 `search_web` 在 `app/agent/tools.py` 里；两边各自都自洽、没有任何行为测试会红。
    真改了形状，这里亮红灯提示你同步对面（并补一条 `test_检索_*` 的用例）。
    """
    src = Path(TOOLS.__file__).read_text(encoding="utf-8")
    assert '"results"' in src, \
        "search_web 的返回结构里找不到 'results' 键 —— 生成侧 _search_all 按它取值，请同步"


# ═══════════════════════════════════════════════════════════════
# 3. 编排（run_web_search 异步生成器）
# ═══════════════════════════════════════════════════════════════

def test_编排_正常产出两条stage与一份料():
    events, outcome = _drive(_StubLLM(), "标题", "正文")
    stages = [p for k, p in events if k == "stage"]
    assert stages[0] == "正在联网检索…", f"第一条 stage 是总提示；实际 {stages}"
    assert "检索词甲" in stages[1] and "检索词乙" in stages[1], \
        f"第二条 stage 要让用户看见抽出的词；实际 {stages}"
    assert outcome.reference is not None and SENTINEL in outcome.reference["content"]
    assert outcome.hits == 4 and outcome.error is None


def test_编排_结果恰好一次且在最后():
    events, _ = _drive(_StubLLM(), "标题", "正文")
    kinds = [k for k, _ in events]
    assert kinds.count("result") == 1, f"result 恰好一次；实际 {kinds}"
    assert kinds[-1] == "result", f"result 必须是最后一个；实际 {kinds}"


def test_编排_抽词失败退化成标题检索并显式报错():
    """抽词失败**不静默**：退化用标题搜，且 outcome.error 让调用方推 warning。

    ⚠️ 对照：把 `outcome.error` 那句删掉 → 「用户勾了开关却什么都没发生」，
    界面零提示（正是「失败显式化」要消灭的形态），本条变红。
    """
    events, outcome = _drive(_StubLLM(raises=True), "我的标题", "正文")
    assert FakeSearch.calls and FakeSearch.calls[0][0] == "我的标题", \
        f"应退化成用标题检索；实际 {FakeSearch.calls}"
    assert outcome.error and "标题" in outcome.error, f"要显式报错；实际 {outcome.error}"
    assert outcome.reference is not None, "退化路径照样能给料"


def test_编排_无标题且抽词失败时直接返回():
    events, outcome = _drive(_StubLLM(raises=True), "", "正文")
    assert outcome.reference is None and outcome.error, f"实际 {outcome}"
    assert FakeSearch.calls == [], "没有检索词就不该发起搜索"
    assert [k for k, _ in events] == ["stage", "result"], f"实际 {events}"


def test_编排_零命中带error且无料():
    FakeSearch.per_query = 0
    _events, outcome = _drive(_StubLLM(), "标题", "正文")
    assert outcome.reference is None and outcome.hits == 0
    assert outcome.error == "联网检索没有返回结果", f"实际 {outcome.error}"


def test_编排_取消时不发起任何调用():
    """用户已取消 ⇒ 不抽词、不搜索（不该再花钱）。

    ⚠️ 对照：去掉开头的 `cancel_event.is_set()` 早退 → `extract_calls` 会非空。
    """
    ev = asyncio.Event()
    ev.set()
    events, outcome = _drive(_StubLLM(), "标题", "正文", cancel_event=ev)
    assert outcome.reference is None and outcome.hits == 0
    assert [k for k, _ in events] == ["stage", "result"], f"实际 {events}"


def test_编排_检索被取消时不报错():
    """用户**自己按了取消**，界面不该弹「联网检索没有返回结果」——那是把我们没做到
    的事报成失败，而实际是用户叫停的。

    ⚠️ **对照**：去掉 `run_web_search` 里 `_search_all` 之后那段取消判断 →
    `_search_all` 因取消返回的 `[]` 被读成「零命中」，`outcome.error` 非空，本条变红。
    """
    FakeSearch.delay = 1.0
    ev = asyncio.Event()

    async def _go():
        asyncio.get_running_loop().call_later(0.05, ev.set)
        events, outcome = [], None
        async for kind, payload in W.run_web_search(_StubLLM(), "标题", "正文", cancel_event=ev):
            events.append((kind, payload))
            if kind == "result":
                outcome = payload
        return events, outcome

    events, outcome = asyncio.run(_go())
    assert outcome is not None and outcome.reference is None
    assert outcome.error is None, f"取消不是失败，不该报错；实际 {outcome.error}"
    assert outcome.hits == 0, f"hits 照记事实；实际 {outcome.hits}"
    assert [k for k, _ in events] == ["stage", "stage", "result"], f"实际 {events}"


def test_抽词_不关闭调用方的llm():
    """⚠️ **绝不 `aclose()`** —— 抽词只是同一次生成里附带的一步，关掉会让紧随其后的
    正文生成拿不到连接池（同 `followup._run_followup_llm` 的纪律，最容易抄错的一行）。
    """
    llm = FakeLLMService(config=None)
    got = asyncio.run(W._extract_queries(llm, "标题", "正文", None))
    assert got == ["检索词甲", "检索词乙"], f"实际 {got}"
    assert llm.closed is False, "抽取检索词不得关闭调用方的 llm"


def test_抽词_失败返回空而不抛():
    FakeLLMService.extract_raises = True
    llm = FakeLLMService(config=None)
    assert asyncio.run(W._extract_queries(llm, "标题", "正文", None)) == [], \
        "抽词失败必须返回空（由调用方退化成标题检索），不能抛"


def test_抽词_超时返回空(monkeypatch):
    # ⚠️ 把超时压到 0.01s，别真等 60 秒
    monkeypatch.setattr(W, "WEB_SEARCH_TIMEOUT", 0.01)
    FakeLLMService.extract_delay = 0.2
    llm = FakeLLMService(config=None)
    got = asyncio.run(W._extract_queries(llm, "标题", "正文", None))
    assert got == [], f"超时应返回空而不是抛；实际 {got}"


def test_抽词_取消置位时不发起调用():
    FakeLLMService.extract_calls = []
    llm = FakeLLMService(config=None)
    ev = asyncio.Event()
    ev.set()
    assert asyncio.run(W._extract_queries(llm, "标题", "正文", ev)) == []
    assert FakeLLMService.extract_calls == [], "取消后不该再发起计费调用"


def test_抽词_有推理能力位时关闭思考():
    """抽两个检索词是纯生成、推理无产出 ⇒ 有档位的模型要**关掉**思考而不是降档。

    ⚠️ 判据（有档位就关、没档位不传）全在 `followup.thinking_extra`，此处只钉住
    「这一步确实用了它」—— 漏传的后果是把输出预算烧在推理链上（分类管道实测过）。
    """
    llm = FakeLLMService(config=None)
    llm.supports_reasoning = True
    FakeLLMService.extract_calls = []
    asyncio.run(W._extract_queries(llm, "标题", "正文", None))
    assert FakeLLMService.extract_calls, "应有一次调用"
    eb = FakeLLMService.extract_calls[-1].get("extra_body")
    assert eb, f"有 reasoning 能力位时必须传 extra_body 关思考；实际 {eb}"

    # 对照：没有该能力位 → **不传**（extra_body=None，逐字等于改造前）
    llm2 = FakeLLMService(config=None)
    FakeLLMService.extract_calls = []
    asyncio.run(W._extract_queries(llm2, "标题", "正文", None))
    assert FakeLLMService.extract_calls[-1].get("extra_body") is None, \
        "没有档位的模型不该传 extra_body"


# ═══════════════════════════════════════════════════════════════
# 4. 端点集成：注入 / 留痕 / 失败不阻断 / 只注 notes
# ═══════════════════════════════════════════════════════════════

def test_勾选时注入笔记并在自证头留痕():
    _mk_doc("ws_yes", "联网基准", "一篇普通网页的正文，讲稀土出口。")
    events = _stream("ws_yes", "mode=notes&web_search=true&model_id=1")

    assert "done" in [e.get("type") for e in events], f"生成必须完成；实际 {events}"
    stages = [e.get("message", "") for e in events if e.get("type") == "stage"]
    assert any("联网检索" in s for s in stages), f"要有检索阶段的提示；实际 {stages}"

    # 真的注进了 prompt（检索结果的唯一标记在场）
    assert FakeLLMService.stream_prompts, "应有正文生成调用"
    joined = "\n".join(FakeLLMService.stream_prompts)
    assert SENTINEL in joined, f"检索结果必须进笔记 prompt；实际 prompt 片段 {joined[:400]}"
    assert "<reference_texts>" in joined, "走的是既有参考资料通道（不是新造的节）"

    # 自证头留痕：queries 与 hits 是「它到底有没有真去搜」的可核对事实
    wp = _web_param("ws_yes")
    assert wp, "自证头必须带 webSearch"
    assert wp["hits"] == 4, f"实际 {wp}"
    assert wp["queries"] == ["检索词甲", "检索词乙"], f"实际 {wp}"
    assert "error" not in wp, f"成功路径不该有 error；实际 {wp}"


def test_不勾选时零行为():
    """⚠️ 对照：勾选那条必须同时红/绿 —— 只测「不勾选就没有」的实现可以靠
    「一律不检索」全绿，那正是最容易写出来的错版本。"""
    _mk_doc("ws_no", "不勾对照", "一篇普通网页的正文。")
    events = _stream("ws_no", "mode=notes&model_id=1")

    assert "done" in [e.get("type") for e in events]
    assert FakeLLMService.extract_calls == [], "没勾就不该抽词（更不该花钱）"
    assert FakeSearch.calls == [], "没勾就不该搜索"
    assert SENTINEL not in "\n".join(FakeLLMService.stream_prompts), \
        "prompt 里不该出现检索结果"
    assert _web_param("ws_no") is None, \
        f"自证头不该有 webSearch 键（缺 ≠ 空对象）；实际 {_web_param('ws_no')}"
    # 反向对照：同一套假件下勾选那条确实做满了（防止本条绿在「功能整体没接线」上）
    _mk_doc("ws_no2", "反向对照", "一篇普通网页的正文。")
    _stream("ws_no2", "mode=notes&web_search=true&model_id=1")
    assert FakeSearch.calls, "对照失败：勾选时必须有搜索调用"


def test_端点_显式关闭联网时不检索():
    """`web_search=false`（前端关掉开关时发的就是它）与「不传」同义 —— 都不检索。

    ⚠️ 上面 `test_不勾选时零行为` 覆盖的是**不传参数**那条路；这条覆盖**显式传 false**。
    两者看起来是一回事，但守的东西不同：这条守的是**将来把参数类型改宽时**的静默反转 ——
    `web_search` 现在是 `bool`，pydantic 会把 `"false"` / `"0"` 都解析成 `False`；
    一旦有人把它改成 `str`（比如为了接受更多取值），`"false"` 就是 **truthy** ⇒
    「关掉开关」反而去检索了，而且**零报错**。
    """
    for q, tx in (("false", "ws_off"), ("0", "ws_off0")):
        _mk_doc(tx, "显式关", "一篇普通网页的正文。")
        events = _stream(tx, f"mode=notes&web_search={q}&model_id=1")
        assert "done" in [e.get("type") for e in events], f"web_search={q}：实际 {events}"
        assert _web_param(tx) is None, \
            f"web_search={q} 不该检索（自证头不该有 webSearch）；实际 {_web_param(tx)}"

    assert FakeLLMService.extract_calls == [], "显式关闭时不该抽词"
    assert FakeSearch.calls == [], "显式关闭时不该搜索"

    # 反向对照：同一套假件下 true 确实做满了（防本条绿在「检索整体没接线」上）
    _mk_doc("ws_off2", "显式开", "一篇普通网页的正文。")
    _stream("ws_off2", "mode=notes&web_search=true&model_id=1")
    assert FakeSearch.calls, "对照失败：web_search=true 时必须有搜索调用"


def test_检索失败只推warning且不阻断生成():
    """检索没生效 ≠ 生成失败 —— 产物照常落盘，界面走 info 提示（不标 partial）。"""
    FakeSearch.raises = True
    FakeLLMService.extract_raises = True  # 抽词也失败 ⇒ 无检索词 ⇒ 直接报错
    _mk_doc("ws_fail", "失败不阻断", "一篇普通网页的正文。")
    events = _stream("ws_fail", "mode=notes&web_search=true&model_id=1")

    warns = [e for e in events if e.get("type") == "warning"]
    assert len(warns) == 1, f"恰好一条 warning；实际 {warns}"
    assert warns[0]["kind"] == "web_search_failed", \
        f"必须是专属 kind（普通 warning 会被前端标成 partial）；实际 {warns[0]}"
    assert "done" in [e.get("type") for e in events], "生成必须照常完成"
    assert _content("ws_fail", "notes").strip(), "正文必须落在盘上"
    wp = _web_param("ws_fail")
    assert wp and wp.get("error"), f"失败也要在自证头留痕；实际 {wp}"


def test_只注入笔记不注入整理版():
    """整理版原文是**忠实整理**，不注入任何参考资料（与参考文件同一契约）。

    ⚠️ 对照：把检索结果也塞进 polished 的 prompt → 含标记的 prompt 会变成 2 个。
    """
    _mk_doc("ws_both", "双产物", "一篇普通网页的正文。")
    events = _stream("ws_both", "mode=notes,polished&web_search=true&model_id=1")
    assert "done" in [e.get("type") for e in events], f"实际 {events}"

    prompts = FakeLLMService.stream_prompts
    assert len(prompts) == 2, f"双产物应有两次生成调用；实际 {len(prompts)}"
    with_hits = [p for p in prompts if SENTINEL in p]
    assert len(with_hits) == 1, f"检索结果只能进笔记那一份；实际 {len(with_hits)} 份含标记"


def test_单产物整理版时不检索():
    """`stream_mode == "polished"` 时整段检索代码在外层 `if` 之外 ⇒ 一次都不发生。"""
    _mk_doc("ws_pol", "只要整理版", "一篇普通网页的正文。")
    events = _stream("ws_pol", "mode=polished&web_search=true&model_id=1")
    assert "done" in [e.get("type") for e in events], f"实际 {events}"
    assert FakeLLMService.extract_calls == [], "只出整理版时不该抽词"
    assert FakeSearch.calls == [], "只出整理版时不该搜索"
    assert _web_param("ws_pol", "polished") is None, \
        "polished 的自证头不该有 webSearch"


def test_非流式端点同样留痕():
    """非流式只写盘、不推事件 —— 但不留痕就变成「勾了却时有时无，取决于走哪条端点」，
    静默不一致正是本项目最忌讳的形态。"""
    _mk_doc("ws_sync", "非流式", "一篇普通网页的正文。")
    r = client.post("/api/generate/ws_sync",
                    json={"mode": "notes", "web_search": True, "model_id": 1})
    assert r.status_code == 200, r.text
    assert SENTINEL in "\n".join(FakeLLMService.call_prompts), \
        "非流式同样要把检索结果注入 prompt"
    wp = _web_param("ws_sync")
    assert wp and wp["hits"] == 4, f"自证头要留痕；实际 {wp}"

    # 对照：不带 web_search 的非流式 → 不检索
    _mk_doc("ws_sync2", "非流式不勾", "一篇普通网页的正文。")
    client.post("/api/generate/ws_sync2", json={"mode": "notes", "model_id": 1})
    assert _web_param("ws_sync2") is None


def test_检索在aclose之前发生():
    """非流式端点的作用域约束：检索用的是同一个 llm，若插在 `await llm.aclose()`
    之后，假实例的 `client` property 会抛错（真实现则是连接池已关、失败被吞）⇒
    用户勾了却什么都搜不到、只剩一条日志。

    ⚠️ 本条靠「假 LLM 的 client 在 closed 后抛错」把那个静默失效变成红灯。
    """
    _mk_doc("ws_scope", "作用域", "一篇普通网页的正文。")
    r = client.post("/api/generate/ws_scope",
                    json={"mode": "notes", "web_search": True, "model_id": 1})
    assert r.status_code == 200, r.text
    wp = _web_param("ws_scope")
    assert wp and wp["hits"] == 4, \
        f"检索必须在 aclose 之前发起；实际 {wp}"
