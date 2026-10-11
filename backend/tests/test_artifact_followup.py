"""产物侧追问卡片 —— 勾选「追问卡片」后，笔记落盘**之后再独立调一次** LLM 产出追问。

⚠️ **本文件是 pytest 形态，不是脚本式**。本仓有一批顶层裸断言的脚本式测试，它们在
`bash scripts/check-all backend`（= `pytest tests/ -q`）下**收集 0 个、静默空跑**——
「断言全过」与「一个用例都没跑」在输出上都是绿的。新增测试一律写 `def test_*`。

⚠️ **不发起真实计费调用**：`LLMService` 被模块级替换为假类。追问走的是
`llm.client.chat.completions.create`（followup.py 直调 SDK），与生成正文走的
`llm.stream` / `llm.call` **不是同一条路**，故假类必须把两者都实现。

⚠️ 本文件的核心纪律（与 `test_generate_gate.py` 同源）：**每条「X 被拦下 / 没发生」
的断言，都要配一条「该发生时确实发生了」的对照**。只测「不勾选就没有追问」的实现，
可以靠「一律不产追问」全绿 —— 那正是最容易写出来的错版本。
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.core.concurrency import ExecutionGuard
from app.core.errors import AppError
from app.core.types import RawMetadata, RawTranscript, SourceType
from app.storage.store import Store

import app.agent.followup as F
import app.routers.generate as G
from app.llm.service import DISABLE_THINKING_EXTRA

_TMP: list[str] = []


@pytest.fixture(scope="module", autouse=True)
def _cleanup():
    yield
    for d in _TMP:
        shutil.rmtree(d, ignore_errors=True)


# ── 假 LLM ───────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, content: str):
        self.choices = [SimpleNamespace(message=SimpleNamespace(content=content))]


class _Completions:
    async def create(self, **kw):
        # ⚠️ 两份记录**都在**：`followup_calls` 存实参（判据要看 `extra_body`），
        # `followup_prompts` 只存 prompt 文本（既有断言一律用它，逐条改没有收益）。
        FakeLLMService.followup_calls.append(kw)
        FakeLLMService.followup_prompts.append(kw["messages"][0]["content"])
        if FakeLLMService.followup_raises:
            raise RuntimeError("模拟追问失败")
        if FakeLLMService.followup_delay:
            await asyncio.sleep(FakeLLMService.followup_delay)
        return _Resp(FakeLLMService.followup_output)


class FakeLLMService:
    """最小替身：生成正文走 `call`/`stream`，追问走 `client.chat.completions.create`。

    ⚠️ **`client` 是带守卫的 property**：`aclose()` 之后再取它会抛错。这不是为了
    仿真，而是让「追问插在 `aclose` 之后」这个作用域错误**可观测** —— 否则假实例的
    关闭是个空操作，那条用例会绿在错误的理由上（这正是非流式端点最易犯的错）。
    """

    #: 类级（端点每次请求都新建实例，记在实例上等于每次从零开始）
    followup_prompts: list[str] = []
    followup_calls: list[dict] = []
    followup_output: str = "- 第一个问题是什么\n- 第二个问题是什么\n- 第三个问题是什么\n- 第四个问题是什么"
    followup_raises: bool = False
    followup_delay: float = 0.0
    stream_prompts: list[str] = []

    def __init__(self, config, model_id=None, request_timeout=None):
        self.provider = "dashscope"
        self.model = "qwen-max"
        self.api_key = "sk-fake-key-for-tests"
        self.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        self.capabilities = ["text"]
        self.supports_reasoning = False
        self.closed = False
        self._client = SimpleNamespace(
            chat=SimpleNamespace(completions=_Completions()))

    @property
    def client(self):
        if self.closed:
            raise RuntimeError("连接池已关闭（追问在 aclose 之后发起）")
        return self._client

    async def call(self, prompt, **kw) -> str:
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
def _reset_fake(monkeypatch):
    """每条用例都从「追问正常产出」的干净状态开始。

    ⚠️ **`G.LLMService` 的替换必须在这里做，不能在模块级做** —— 它是**模块级共享
    全局**，`test_generate_gate.py`（文件头自称「模块级替换为假类」）等文件同样替换它。
    pytest 在**收集阶段** import 全部测试模块，每个文件都在 import 时执行
    `G.LLMService = <自己的假类>` ⇒ **最后 import 的那个赢**。
    症状正是「本文件单跑 22 条全绿、放进 `tests/` 全量跑则 8 条红」，且红的形态是
    「追问返回空」而非断言写错 —— 跑用例时拿到的根本不是 `FakeLLMService`。
    修正后「单跑绿 / 全量红」这个分叉本身也就成了隔离性的证据。

    ⚠️ 类属性（`followup_output` 等）跨用例共享，同样要复位 —— 否则「上一轮设了
    失败/延迟」会渗到下一轮，用例之间产生顺序耦合。
    """
    monkeypatch.setattr(G, "LLMService", FakeLLMService)
    FakeLLMService.followup_prompts = []
    FakeLLMService.followup_calls = []
    FakeLLMService.stream_prompts = []
    FakeLLMService.followup_output = ("- 第一个问题是什么\n- 第二个问题是什么\n"
                                      "- 第三个问题是什么\n- 第四个问题是什么")
    FakeLLMService.followup_raises = False
    FakeLLMService.followup_delay = 0.0
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


def _fu_path(tx_id: str, mode: str, ver: int) -> Path:
    return Path(_STORE.get_doc_dir(tx_id)) / mode / f"v{ver}.followups.json"


def _all_fu_files(tx_id: str) -> list[str]:
    d = Path(_STORE.get_doc_dir(tx_id))
    # ⚠️ 用 as_posix()：Windows 下 str(Path) 是反斜杠，断言里写 "notes/v1..." 会永远不等
    return sorted(p.relative_to(d).as_posix() for p in d.rglob("*.followups.json"))


# ═══════════════════════════════════════════════════════════════
# 1. 勾选 → 落盘 + 内容一致（正向基准，后面的「不产」都以它为对照）
# ═══════════════════════════════════════════════════════════════

def test_勾选时写出旁路文件且内容与事件一致():
    _mk_doc("fu_yes", "追问基准", "一篇普通网页的正文。")
    events = _stream("fu_yes", "mode=notes&formats=followup&model_id=1")

    fu_events = [e for e in events if e.get("type") == "followup"]
    assert len(fu_events) == 1, f"勾了就该推恰好一个 followup 事件；实际 {events}"
    ev = fu_events[0]
    assert ev["mode"] == "notes" and ev["version"] == 1, f"事件要能定位到版本；实际 {ev}"
    assert ev["error"] is None, f"正常路径不该带 error；实际 {ev}"
    assert len(ev["questions"]) == 4, f"应原样带出模型给的问题；实际 {ev['questions']}"

    # 落盘位置是**旁路文件**（与 v1.md 并排），不是正文、不是 index、不是自证头
    assert _all_fu_files("fu_yes") == ["notes/v1.followups.json"], \
        f"旁路文件必须落在 notes/v1.followups.json；实际 {_all_fu_files('fu_yes')}"
    saved = json.loads(_fu_path("fu_yes", "notes", 1).read_text(encoding="utf-8"))
    assert saved["questions"] == ev["questions"], "盘上的与事件里的必须一致"
    assert saved["v"] == 1, "带版本号便于将来演进"

    # ⚠️ **正文里一个字都不能有**：追问住在旁路文件，正是为了导出/搜索天然干净
    assert "第一个问题是什么" not in _content("fu_yes", "notes"), \
        "追问绝不能进正文（否则 .md 导出会把候选问句一起带走）"


# ═══════════════════════════════════════════════════════════════
# 2. 对照：不勾选 → 既不落盘也不推事件
# ═══════════════════════════════════════════════════════════════

def test_不勾选时既不落盘也不推事件():
    _mk_doc("fu_no", "不勾对照", "一篇普通网页的正文。")
    events = _stream("fu_no", "mode=notes&model_id=1")

    assert "done" in [e.get("type") for e in events], f"生成必须正常完成；实际 {events}"
    assert not [e for e in events if e.get("type") == "followup"], \
        f"没勾就不该有 followup 事件；实际 {events}"
    assert _all_fu_files("fu_no") == [], "没勾就不该写旁路文件"
    assert FakeLLMService.followup_prompts == [], \
        "没勾就**一次追问调用都不该发起**（省下的正是钱）"


# ═══════════════════════════════════════════════════════════════
# 3-4. 两个闸门：composite / 只出整理版 —— 都不产追问
# ═══════════════════════════════════════════════════════════════

def test_合成文档不产追问_流式format_list被清空():
    _mk_doc("fu_comp", "甲乙对比", "【文档 1】甲\n正文甲", SourceType.COMPOSITE)
    events = _stream("fu_comp", "mode=notes&formats=followup&model_id=1")

    assert "done" in [e.get("type") for e in events], f"生成必须正常完成；实际 {events}"
    assert not [e for e in events if e.get("type") == "followup"], \
        "合成文档的 format_list 被清空 ⇒ 不产追问（对照 §1 证明这条件不是恒真）"
    assert _all_fu_files("fu_comp") == []


def test_只生成整理版时不产追问_非流式():
    _mk_doc("fu_pol", "只要整理版", "一篇普通网页的正文。")
    r = client.post("/api/generate/fu_pol",
                    json={"mode": "polished", "formats": ["followup"], "model_id": 1})
    assert r.status_code == 200, r.text
    assert _all_fu_files("fu_pol") == [], \
        "追问只作用于笔记（用户已定）—— 只出整理版时不该产"
    assert FakeLLMService.followup_prompts == []


# ═══════════════════════════════════════════════════════════════
# 5. 追问失败 → 事件带 error、不写盘、**流程照常结束**（不阻断）
# ═══════════════════════════════════════════════════════════════

def test_追问失败时显式报错且不阻断流程():
    _mk_doc("fu_fail", "追问失败", "一篇普通网页的正文。")
    FakeLLMService.followup_raises = True
    events = _stream("fu_fail", "mode=notes&formats=followup&model_id=1")

    ev = next((e for e in events if e.get("type") == "followup"), None)
    assert ev is not None, f"失败也必须推事件（静默失败 = 勾了却什么都没有）；实际 {events}"
    assert ev["error"], f"失败必须显式带 error；实际 {ev}"
    assert ev["questions"] == [], "失败时没有问题"
    assert _all_fu_files("fu_fail") == [], "失败不该留下半个文件"
    # ⚠️ 正文照常落盘、流程照常结束：追问是**附属产物**，它失败不该牵连主产物
    assert _content("fu_fail", "notes"), "追问失败不该影响笔记落盘"
    assert "done" in [e.get("type") for e in events], "追问失败不该让整条流断掉"


# ═══════════════════════════════════════════════════════════════
# 6-7. 时序：推在 done **之后**，且**先写盘再推事件**
# ═══════════════════════════════════════════════════════════════

def test_追问事件推在done之后():
    _mk_doc("fu_order", "事件顺序", "一篇普通网页的正文。")
    types = [e.get("type") for e in _stream("fu_order", "mode=notes&formats=followup&model_id=1")]

    assert types[-1] == "followup", f"追问必须是最后一个事件；实际 {types}"
    assert types.index("done") < types.index("followup"), \
        f"`done` 必须先到（用户此刻已能继续操作，追问是后补的卡）；实际 {types}"


def test_先写盘再推事件_事件到达时文件已可读():
    _mk_doc("fu_persist", "先写盘", "一篇普通网页的正文。")
    buf = ""
    got = False

    with client.stream(
            "GET", "/api/generate/fu_persist/stream?mode=notes&formats=followup&model_id=1") as r:
        for chunk in r.iter_text():
            buf += chunk
            # ⚠️ 判据必须在**流还没读完时**取：读完之后文件当然在，那证明不了顺序。
            #    反过来写（先推事件后写盘）时，这里会读到「文件还不存在」。
            if not got and '"type": "followup"' in buf:
                got = True
                assert _all_fu_files("fu_persist") == ["notes/v1.followups.json"], \
                    "事件到达时文件就该已经在了（先推后写的实现会红）"

    assert got, "没有收到 followup 事件"


# ═══════════════════════════════════════════════════════════════
# 8. 读取层：有条目才给 followUps 键（**缺 ≠ 空数组**）
# ═══════════════════════════════════════════════════════════════

def test_读取层有才给键无则不给键():
    _mk_doc("fu_read", "读取层", "一篇普通网页的正文。")
    client.post("/api/generate/fu_read",
                json={"mode": "notes", "formats": ["followup"], "model_id": 1})

    entries = {e["mode"]: e for e in _STORE.get_markdowns_by_transcript("fu_read")}
    assert entries["notes"].get("followUps"), \
        f"有文件就必须带出 followUps；实际 {entries['notes'].keys()}"

    # 对照：另建一篇没追问的 → **键不存在**（不是空数组）
    _mk_doc("fu_read2", "无追问", "一篇普通网页的正文。")
    client.post("/api/generate/fu_read2", json={"mode": "notes", "model_id": 1})
    e2 = {e["mode"]: e for e in _STORE.get_markdowns_by_transcript("fu_read2")}
    assert "followUps" not in e2["notes"], \
        ("缺与空数组合义不同（前者=这一版没有，后者=有问题但零条）——"
         f"一律补成 [] 的实现会红；实际键 {sorted(e2['notes'].keys())}")


def test_旁路文件损坏时读取层不抛异常():
    _mk_doc("fu_broken", "坏文件", "一篇普通网页的正文。")
    client.post("/api/generate/fu_broken",
                json={"mode": "notes", "formats": ["followup"], "model_id": 1})
    _fu_path("fu_broken", "notes", 1).write_text("{ 这不是 JSON", encoding="utf-8")

    assert _STORE.read_artifact_followups("fu_broken", "notes", 1) == [], \
        "坏掉的旁路文件只该被忽略（旁路文件坏掉不该挡阅读）"
    entries = {e["mode"]: e for e in _STORE.get_markdowns_by_transcript("fu_broken")}
    assert "followUps" not in entries["notes"], "读不出来就等于没有，不崩"


# ═══════════════════════════════════════════════════════════════
# 9. 追问 prompt：含标题、**不含**渲染协议标记
# ═══════════════════════════════════════════════════════════════

def test_追问prompt带标题且剥掉limo_block标记():
    _mk_doc("fu_prompt", "我的论文标题", "一篇普通网页的正文。")
    p = F.build_artifact_followup_prompt("我的论文标题", "正文\n<!-- limo-block:v1 -->\n块内文字\n<!-- /limo-block -->")
    assert "我的论文标题" in p, "必须把标题喂给模型（它决定追问的角度）"
    assert "limo-block" not in p, \
        "渲染协议标记必须先被 strip_blocks 剥掉（模型看了会困惑且白占预算）"
    assert "块内文字" in p, "剥标记但**内容一字不丢**"


def test_两侧prompt共用同一份条款常量():
    """源码守卫：条款只写一份，两个 prompt 都从它派生。

    ⚠️ 走**源码**而不是「比较两个 prompt 的字符串」：后者在把条款复制成两份时
    照样成立（两边内容一样就相等）—— 而复制恰恰是这里要防的事。
    """
    src = Path(F.__file__).read_text(encoding="utf-8")
    assert src.count("从读者视角出发 —— 读者读完上面这段后自然想知道的") == 1, \
        "条款只该在 FOLLOWUP_RULES 里出现一次（复制成两份的实现会红）"
    # ⚠️ 判据是**条款内容**在两者里都出现，而不是字面量 `"FOLLOWUP_RULES"` ——
    #    `+ FOLLOWUP_RULES` 拼接后那串字面量根本不在结果里，写成字面量 `in` 会恒红
    assert F.FOLLOWUP_RULES in F.FOLLOWUP_PROMPT, "对话侧 prompt 必须由 FOLLOWUP_RULES 派生"
    assert F.FOLLOWUP_RULES in F.ARTIFACT_FOLLOWUP_PROMPT, "产物侧 prompt 必须由同一份条款派生"


# ═══════════════════════════════════════════════════════════════
# 10. 那一层调用的语义：不 aclose / 异常吞掉 / 取消置位不发起
# ═══════════════════════════════════════════════════════════════

def test_追问调用不关闭调用方的llm():
    llm = FakeLLMService({})
    asyncio.run(F.generate_artifact_followups(llm, "标题", "正文"))
    assert llm.closed is False, \
        "追问必须复用调用方的连接池且不关它（照抄 compress.py 的 aclose 会红）"

    # 对照：证明 `closed` 这个哨兵是活的
    asyncio.run(llm.aclose())
    assert llm.closed is True


def test_追问异常与超时都返回None不抛():
    llm = FakeLLMService({})

    FakeLLMService.followup_raises = True
    assert asyncio.run(F.generate_artifact_followups(llm, "标题", "正文")) is None, \
        "异常必须被吞掉（不阻塞主流程是第一位的）"

    # 超时：把模块级超时压到极小，并让假 client 睡过去
    FakeLLMService.followup_raises = False
    FakeLLMService.followup_delay = 0.2
    real_timeout = F.FOLLOWUP_TIMEOUT
    F.FOLLOWUP_TIMEOUT = 0.05
    try:
        assert asyncio.run(F.generate_artifact_followups(llm, "标题", "正文")) is None, \
            "超时必须返回 None（追问有独立的一层超时，不与他人共用）"
    finally:
        F.FOLLOWUP_TIMEOUT = real_timeout

    # 模型回了东西但不是列表（空壳）→ 也当失败
    FakeLLMService.followup_delay = 0.0
    FakeLLMService.followup_output = "我觉得这几个问题不错。"
    assert asyncio.run(F.generate_artifact_followups(llm, "标题", "正文")) is None, \
        "抽不出列表项就该当失败（否则前端会拿到一张空壳卡）"


def test_取消置位时不发起追问调用():
    llm = FakeLLMService({})
    FakeLLMService.followup_prompts = []
    ev = asyncio.Event()
    ev.set()
    assert asyncio.run(F.generate_artifact_followups(llm, "标题", "正文",
                                                     cancel_event=ev)) is None
    assert FakeLLMService.followup_prompts == [], \
        "用户已取消 ⇒ 一次调用都不该发起（不该再花一次钱）"


def test_追问按模型档位决定思考参数():
    """产物侧走的是**与对话侧同一份判据**（`_run_followup_llm` 两侧共用），本用例把
    两条分支都钉住 —— 只断「有 extra_body」会让「无条件塞常量」的实现全绿，而那正是
    「没有的东西假装有」（用户 2026-10-11 定：有档位搞最低，没有走默认）。

    ⚠️ 有意思的是**本文件的假类恰好是 qwen-max**（`supports_reasoning=False`），
    于是「无档位 → 不传」这条是走真实形态、而「有档位 → 关闭」要临时改属性。
    """
    llm = FakeLLMService({})                     # qwen-max：没有档位这一说
    asyncio.run(F.generate_artifact_followups(llm, "标题", "正文"))
    assert FakeLLMService.followup_calls[0]["extra_body"] is None, \
        "无档位的模型一个字段都不该收到"

    FakeLLMService.followup_calls = []
    llm.supports_reasoning = True                # DeepSeek V4 系
    asyncio.run(F.generate_artifact_followups(llm, "标题", "正文"))
    extra = FakeLLMService.followup_calls[0]["extra_body"]
    assert extra is DISABLE_THINKING_EXTRA, \
        "有档位 ⇒ 关闭思考，且必须**引用**共用常量（照抄字面量会红）"
    assert "reasoning_effort" not in extra, \
        "关闭与档位在服务端互为替代 —— 同传会被整次请求拒绝"


# ═══════════════════════════════════════════════════════════════
# 11-14. 端到端：双产物的三个 done 出口 / 挂在 notes 版本 / 作用域
# ═══════════════════════════════════════════════════════════════

def test_双产物追问挂在notes那一版():
    """追问只挂 notes，且版本号取自 **notes 那一版**。

    ⚠️ **必须先造出「polished 版本号高于 notes」的真实局面**，这条才有区分力：
    双产物是两条几乎同刻写入、**版本号都从 1 起**，`_ver` 不过滤 mode 也可能碰巧取到
    notes —— 那样一条「去掉过滤 → 仍绿」的用例等于没测。
    这里先单独生成两次整理版（polished v1 / v2），再走双产物（notes v1）：
    不过滤 mode 时降序第一是 polished **v2** ⇒ 会写出 `notes/v2.followups.json`。
    """
    _mk_doc("fu_both", "双产物", "一篇普通网页的正文。")
    _stream("fu_both", "mode=polished&model_id=1")
    _stream("fu_both", "mode=polished&model_id=1")
    events = _stream("fu_both", "mode=both&formats=followup&model_id=1")

    assert "done" in [e.get("type") for e in events], f"双产物必须正常完成；实际 {events}"
    ent = {(e.get("mode"), e.get("version")) for e in _STORE.list_markdown_entries("fu_both")}
    assert ("polished", 2) in ent and ("notes", 1) in ent, \
        f"夹具前提：polished 到 v2 而 notes 只有 v1（否则本用例无区分力）；实际 {ent}"
    assert _all_fu_files("fu_both") == ["notes/v1.followups.json"], \
        ("追问只能挂在 notes 版、且版本号必须**按 mode 过滤**后再取；"
         f"实际 {_all_fu_files('fu_both')}")


def test_双产物单路失败时追问仍产出_三个done出口都覆盖():
    """notes 成功 + polished 失败 → 走 `one_failed` 出口，**追问仍要有**。

    ⚠️ 双产物有三个 `done` 出口（空产物 / 单路失败 / 正常）汇到同一个 `return`。
    若把追问插在某一支 `done` 之后而不是 `return` 之前，这一支就会漏 —— 而
    「正常」那条用例照样全绿。
    """
    _mk_doc("fu_partial", "单路失败", "一篇普通网页的正文。")
    real = FakeLLMService.stream

    def _stream_partial(self, prompt, **kw):
        # 判据是**角色文案**（并发两路谁先调到 stream 不保证，不能用顺序）
        if "专业的内容编辑" in prompt:
            return _ExplodingStream()
        return real(self, prompt, **kw)

    FakeLLMService.stream = _stream_partial
    try:
        events = _stream("fu_partial", "mode=both&formats=followup&model_id=1")
    finally:
        FakeLLMService.stream = real

    assert "done" in [e.get("type") for e in events], f"单路失败不该断流；实际 {events}"
    assert _all_fu_files("fu_partial") == ["notes/v1.followups.json"], \
        ("单路失败这条出口同样要产追问（插在 done 之后而非 return 之前的实现会漏掉它）；"
         f"实际 {_all_fu_files('fu_partial')}")


class _ExplodingStream:
    """**一个 chunk 都不吐**就抛 —— 模拟「这一路彻底失败」。

    ⚠️ **刻意不先吐一段**：吐了就会被当成「写了一半」，路由把 partial 存下来并认为
    这一路**有产物** —— 那时「这一路失败」的前提就不成立了，用例会红在**错误的理由**
    上（看起来像被测代码有 bug，实际是夹具没造出它自己声称的那个场景）。
    """

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise RuntimeError("模拟这一路生成失败")

    async def aclose(self):
        pass


def test_笔记失败时不产追问且不抛NameError():
    """notes 一路全失败 → `_notes_for_followup` 保持空串 → 不追问，收尾照常。

    ⚠️ 这条守的是**作用域**：`notes_content` 只在 `if notes_chunks:` 块内赋值，
    在 `done` 决策处直接引用它会抛 `NameError`（`tests/test_closure_shadowing.py`
    防的正是这一类）。修法是预置 `_notes_for_followup = ""`。
    """
    _mk_doc("fu_noNotes", "笔记失败", "一篇普通网页的正文。")
    real = FakeLLMService.stream

    def _stream_notes_boom(self, prompt, **kw):
        if "专业的内容编辑" in prompt:
            return real(self, prompt, **kw)     # polished 正常
        return _ExplodingStream()               # notes 断流

    FakeLLMService.stream = _stream_notes_boom
    try:
        events = _stream("fu_noNotes", "mode=both&formats=followup&model_id=1")
    finally:
        FakeLLMService.stream = real

    assert "done" in [e.get("type") for e in events], f"收尾必须照常；实际 {events}"
    assert not [e for e in events if e.get("type") == "followup"], "notes 没落盘就不该追问"
    assert _all_fu_files("fu_noNotes") == []
    assert FakeLLMService.followup_prompts == [], "一次调用都不该发起"


def test_版本号无法确定时显式报错且不落盘(monkeypatch):
    """`_ver == 0` 防御分支 —— 写 `v0.followups.json` 是**读不回来的垃圾**。"""
    _mk_doc("fu_v0", "版本号异常", "一篇普通网页的正文。")
    monkeypatch.setattr(_STORE, "list_markdown_entries", lambda tx_id: [])

    events = _stream("fu_v0", "mode=notes&formats=followup&model_id=1")

    ev = next((e for e in events if e.get("type") == "followup"), None)
    assert ev is not None, f"必须推事件说明原因；实际 {events}"
    assert ev["error"], f"版本号取不到要显式报错；实际 {ev}"
    assert ev["version"] == 0
    assert _all_fu_files("fu_v0") == [], \
        f"绝不能写出 v0.followups.json（读取路径永远够不到它）；实际 {_all_fu_files('fu_v0')}"


# ═══════════════════════════════════════════════════════════════
# 15-16. 非流式端点：同样写盘，且**必须插在 aclose 之前**
# ═══════════════════════════════════════════════════════════════

def test_非流式端点同样写盘():
    _mk_doc("fu_sync", "非流式", "一篇普通网页的正文。")
    r = client.post("/api/generate/fu_sync",
                    json={"mode": "notes", "formats": ["followup"], "model_id": 1})
    assert r.status_code == 200, r.text
    assert _all_fu_files("fu_sync") == ["notes/v1.followups.json"], \
        ("非流式端点也要产追问 —— 否则「勾了却时有时无，取决于走哪条端点」，"
         f"正是静默不一致；实际 {_all_fu_files('fu_sync')}")


def test_非流式端点的追问插在aclose之前():
    """作用域守卫：假 LLM 的 `client` 在 `aclose()` 之后会抛错。

    放到 `finally` 之后 = 追问拿着已关闭的连接池失败，而失败被 `_run_followup_llm`
    **刻意吞掉**（为的是不阻塞主流程）⇒ 用户勾了却什么都没有、只剩一条 warning 日志、
    **零报错**。本用例把那条静默失效变成红灯。
    """
    _mk_doc("fu_scope", "作用域", "一篇普通网页的正文。")
    r = client.post("/api/generate/fu_scope",
                    json={"mode": "notes", "formats": ["followup"], "model_id": 1})
    assert r.status_code == 200, r.text
    assert _all_fu_files("fu_scope") == ["notes/v1.followups.json"], \
        ("追问必须在 `await llm.aclose()` **之前**发起（放在 finally 之后的实现会红）；"
         f"实际 {_all_fu_files('fu_scope')}")


def test_非流式端点不产followup事件也不产polished追问():
    """非流式只写盘 —— 它 `return` 一个 dict，没有 SSE 可言。"""
    _mk_doc("fu_sync2", "非流式无事件", "一篇普通网页的正文。")
    r = client.post("/api/generate/fu_sync2",
                    json={"mode": "notes", "formats": ["followup"], "model_id": 1})
    body = r.json()
    assert "followup" not in json.dumps(body, ensure_ascii=False), \
        "非流式响应里不该有事件形状的东西"
    # 对照：同一篇走流式 → 有事件（证明上面那句不是恒真）
    events = _stream("fu_yes", "mode=notes&formats=followup&model_id=1")
    assert [e for e in events if e.get("type") == "followup"], "对照失败：流式必须推事件"


def test_自证头里记录followup格式项():
    """⚠️ 已知并有意的副作用：`format_list` 整份进了自证头的 `params.formats`。

    它确实是登记在案的格式项，故**接受**；但这意味着读自证头的人可能以为
    「模型被要求产出追问」。文档上写明「登记但零注入」（prompts-inventory §9.6），
    本用例把这条副作用钉住 —— 将来若被过滤掉，会在这里红，而不是悄悄改变语义。
    """
    _mk_doc("fu_meta", "自证头", "一篇普通网页的正文。")
    client.post("/api/generate/fu_meta",
                json={"mode": "notes", "formats": ["followup"], "model_id": 1})
    raw = _content("fu_meta", "notes")
    first = raw.splitlines()[0] if raw else ""
    assert first.startswith("<!-- limo-meta"), f"产物首行应是自证头；实际 {first[:60]!r}"
    assert '"followup"' in first, \
        f"format_list 整份进自证头是既定行为（改动它会改变语义）；实际 {first[:200]}"
