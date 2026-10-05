"""验证生成端点的**单产物闸门** — composite / xlsx 的产物裁剪 / 格式清空 / 出处头损坏提示

⚠️ **不发起真实计费调用**：`LLMService` 被模块级替换为假类，只记调用次数。

⚠️ 本文件的核心纪律：闸门写宽了会**静默毁掉既有类型的行为**。所以每一条
「composite 被拦下」的断言，都必须配一条「普通文档行为不变」的对照——只测前者
的实现可以靠「一律砍掉 polished」全绿。

⚠️ **xlsx 与 composite 的两条闸门理由不同、结果相同，各自独立**（§7）：composite 是
「rawText 已是定稿输入，polished 只会改写它」，xlsx 是「Excel 的语义本就只有数据摘要
**一个**产物」。合并成 `if source_type in (...)` 会掩盖两条理由，任一条失效时也看不出
是哪条——所以本文件对两者各测各的。
"""
import asyncio
import copy
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.core.errors import AppError
from app.core.transcript_meta import build_composite_raw_text
from app.core.types import MarkdownView, RawMetadata, RawTranscript, SourceType
from app.core.concurrency import ExecutionGuard
from app.storage.store import Store

import app.routers.generate as G

_TMP: list[str] = []


def _new_store() -> Store:
    d = tempfile.mkdtemp()
    _TMP.append(d)
    return Store(d)


class _FakeStream:
    """`llm.stream()` 的替身：异步迭代 + `aclose()`（路由的 finally 会调它）。"""

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


class FakeLLMService:
    """最小替身：只提供路由与 Generator 真正取用的那几个属性/方法。"""

    def __init__(self, config, model_id=None, request_timeout=None):
        self.provider = "dashscope"
        self.model = "qwen-max"
        self.api_key = "sk-fake-key-for-tests"
        self.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        self.capabilities = ["text"]
        self.supports_reasoning = False
        self.calls: list[str] = []

    async def call(self, prompt, **kw) -> str:
        self.calls.append(prompt)
        return "# 生成的笔记\n\n这是模型产出的正文。"

    def stream(self, prompt, **kw) -> _FakeStream:
        self.calls.append(prompt)
        return _FakeStream(["# 生成的笔记", "\n\n这是模型产出的正文。"])


# ── 测试 app：只挂 generate 路由（不触发主 app startup，避免拉真实配置/插件）──
_STORE = _new_store()
_CFG = {
    "llm": {"provider": "dashscope", "model": {"dashscope": "qwen-max"},
            "api_key": "sk-fake-key-for-tests",
            "base_url": {"dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1"}},
    "target_language": "zh-CN",
    # ⚠️ 必须给一个 enabled_model：`resolve_context_window` 的第 1 层就是它，
    # 第 2/3 层要看 provider/model_name 能否解析出来——空列表会直接落到保守 32k，
    # 而 composite 在 32k 下会被 multi 闸门拒绝（测试看起来像"闸门没生效"）。
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


G.LLMService = FakeLLMService          # 模块级替换：路由内 `LLMService(...)` 即得假实例
client = TestClient(app)


def _mk_doc(tx_id: str, title: str, raw: str, stype: SourceType) -> None:
    """写入一篇测试文档。

    ⚠️ `skip_dedup=True` 不是图方便：本文件有两个文档共用同一份 `_COMP_RAW`
    （闸门测试 + 自动标题测试），而 `save_transcript` 默认按 `sha256(raw_text)`
    去重 → 第二篇**静默不落库**，症状是几行之后 `get_transcript()` 返回 None，
    看起来像「闸门把这些文档搞没了」。合成端点的真实路径也是 `skip_dedup=True`。
    """
    _STORE.save_transcript(RawTranscript(
        id=tx_id, title=title, source_type=stype, engine="test",
        raw_text=raw, content_hash="", metadata=RawMetadata(), tags=[]),
        skip_dedup=True)


def _modes(tx_id: str) -> list[str]:
    return sorted({m["mode"] for m in _STORE.get_markdowns_by_transcript(tx_id)})


# ── 素材 ──
_COMP_RAW = build_composite_raw_text(
    ["tr_src1", "tr_src2"], ["aaaa1111", "bbbb2222"],
    [{"title": "论文甲", "status": "full"}, {"title": "论文乙", "status": "summarized"}],
    "【文档 1】论文甲\n正文甲\n\n【文档 2】论文乙\n正文乙",
)
_mk_doc("tr_comp", "甲乙对比", _COMP_RAW, SourceType.COMPOSITE)
_mk_doc("tr_plain", "普通网页", "这是一篇普通网页的正文。", SourceType.WEBPAGE)

# ═══════════════════════════════════════════════════════════════
# 1. composite + mode=both → 只落 notes，不产 polished
# ═══════════════════════════════════════════════════════════════

_r = client.post("/api/generate/tr_comp", json={"mode": "both", "formats": ["toc"], "model_id": 1})
assert _r.status_code == 200, _r.text
_modes_comp = _modes("tr_comp")
assert _modes_comp == ["notes"], \
    f"合成文档只该产 notes，实际 {_modes_comp}（无闸门的实现会多出 polished）"
print("PASS: composite + mode=both → 只产 notes（polished 未被写盘）")

# ═══════════════════════════════════════════════════════════════
# 2. 对照：普通文档 + mode=both 行为**不变**（两条都产）
# ═══════════════════════════════════════════════════════════════

_r2 = client.post("/api/generate/tr_plain", json={"mode": "both", "model_id": 1})
assert _r2.status_code == 200, _r2.text
_modes_plain = _modes("tr_plain")
assert _modes_plain == ["notes", "polished"], \
    f"普通文档必须照旧产两份，实际 {_modes_plain}（闸门条件写宽的实现会丢 polished）"
print("PASS: 对照 —— 普通文档 mode=both 仍产 notes+polished（闸门没写宽）")

# ═══════════════════════════════════════════════════════════════
# 3. composite + formats=["chart"] → 不触发图表（format_list 被清空）
#
# ⚠️ **两条路径各有各的清空语句，必须各测各的**：流式端点 :516 清 `format_list`，
# 非流式端点 :257 清 `formats_list`。把任一处改成 `if False:`（等于没清），
# **另一处的测试照样全绿**——实测确认过两次。
#
# ⚠️⚠️ 更要紧的是**判据不能选在 prompt 上**：composite 的图表规范注入本来就
# **被 prompt 层挡着**（`get_chart_format` 只在 `AVAILABLE_OPTIONS` 允许的
# source_type 下注入，composite 不在其中）。于是「composite 的 prompt 里没有
# ```fig」**恒为真**，与 format_list 清没清无关——第一版就是这么写的，把非流式的
# 清空删掉它照样 PASS。本轮换成两条**各自路径独有**的产物级判据：
#   3b 非流式 → 模型输出 ```fig 时**不渲染 PNG**（对照：普通文档渲染出 chart_1.png）
#   3c 流式   → **不推** `chart_missing` 警告（对照：普通文档推，见 :1032）
# 3a 只留「普通文档仍注入规范」这条对照（防「一律清空」），不再为 composite 断 prompt。
# ═══════════════════════════════════════════════════════════════

_calls: list[str] = []
_orig_call = FakeLLMService.call


async def _capture(self, *a, **kw):
    _calls.append(a[0] if a else kw.get("prompt", ""))
    return "# 笔记\n\n正文"


# ── 3a（对照）：普通文档勾图表仍**注入**规范 —— 防「一律清空 format_list」 ──
FakeLLMService.call = _capture
try:
    _r3 = client.post("/api/generate/tr_plain",
                      json={"mode": "notes", "formats": ["chart"], "model_id": 1})
finally:
    FakeLLMService.call = _orig_call
assert _r3.status_code == 200, _r3.text
assert _calls and any("```fig" in p or "```chart" in p for p in _calls), \
    "对照失败：普通文档勾图表必须仍注入图表规范（一律清空的实现会红）"
print("PASS: [3a 对照] 普通文档仍注入图表规范（防「一律清空」）")

# ── 3b（非流式，核心）：模型输出 ```fig → composite **不渲染** PNG ──
# 假模型改为吐一段合法的 fig 围栏，模拟「模型自发画了图」。这是非流式路径上
# `formats_list` 唯一可观测的出口：`generator.generate` 的
# `if formats and "chart" in formats:` → `render_charts` 写 `assets/chart_N.png`。
# （非流式**没有** chart_missing 兜底，那道只在流式端点 :845 / :1032。）
_CHART_BODY = ('# 笔记\n\n'
               '```fig\n{"type":"hchain","nodes":[{"label":"甲"},{"label":"乙"}]}\n```\n')


async def _capture_chart(self, *a, **kw):
    _calls.append(a[0] if a else kw.get("prompt", ""))
    return _CHART_BODY


def _chart_pngs(tx_id: str) -> list[str]:
    _assets = Path(_STORE.get_doc_dir(tx_id)) / "assets"
    return sorted(p.name for p in _assets.glob("chart_*.png")) if _assets.is_dir() else []


FakeLLMService.call = _capture_chart
try:
    _r3b = client.post("/api/generate/tr_comp",
                       json={"mode": "notes", "formats": ["chart"], "model_id": 1})
    _r3c = client.post("/api/generate/tr_plain",
                       json={"mode": "notes", "formats": ["chart"], "model_id": 1})
finally:
    FakeLLMService.call = _orig_call
assert _r3b.status_code == 200 and _r3c.status_code == 200, _r3b.text
assert _chart_pngs("tr_comp") == [], \
    f"composite 不该渲染图表（漏清 formats_list 的实现会红）；实际 {_chart_pngs('tr_comp')}"
assert _chart_pngs("tr_plain"), \
    "对照失败：普通文档勾图表 + 模型输出 ```fig → 必须渲染出 chart_*.png"
print(f"PASS: [3b 非流式] composite 不渲染图表（对照：普通文档产出 {_chart_pngs('tr_plain')}）")

# ── 3c（流式）：`format_list` 里那个 "chart" 还驱动 :1032 的完成期兜底：
# 「勾了图表但模型没输出图表围栏」→ 推 `chart_missing` 警告。
# 假模型返回纯文本、必然无围栏 → **清了 format_list 就不该有这个警告**。
with client.stream("GET", "/api/generate/tr_comp/stream?mode=notes&formats=chart&model_id=1") as _rs3d:
    _b3d = "".join(_rs3d.iter_text())
assert "done" in _b3d, f"流式请求必须正常完成；实际 {_b3d[:300]}"
assert "chart_missing" not in _b3d, \
    "composite 的 format_list 没被清空（流式路径漏清 → 会误报「图表未生成」）"
# 对照：普通文档走同一端点、同一 formats → **必须**推 chart_missing（证明这条出口是活的）
with client.stream("GET", "/api/generate/tr_plain/stream?mode=notes&formats=chart&model_id=1") as _rs3e:
    _b3e = "".join(_rs3e.iter_text())
assert "chart_missing" in _b3e, \
    "对照失败：普通文档勾图表而模型未出图时，必须照旧推 chart_missing"
print("PASS: [3c 流式] composite 不推 chart_missing（对照：普通文档仍推）")

# ═══════════════════════════════════════════════════════════════
# 4. 出处头损坏 → 流式推 metadata_corrupt 提示，且**不阻断生成**
# ═══════════════════════════════════════════════════════════════

_CORRUPT_RAW = _COMP_RAW.replace('{"sourceType":"composite"', '{"sourceType":BROKEN', 1)
assert _CORRUPT_RAW != _COMP_RAW, "必须先确认真的把 JSON 改坏了"
_mk_doc("tr_corrupt", "损坏的合成", _CORRUPT_RAW, SourceType.COMPOSITE)
_tx = _STORE.get_transcript("tr_corrupt")
assert _tx.get("metadataCorrupt") is True, "前提：该文档必须被标记为 metadataCorrupt"

with client.stream("GET", "/api/generate/tr_corrupt/stream?mode=notes&model_id=1") as _rs:
    assert _rs.status_code == 200, _rs.status_code
    _body = "".join(_rs.iter_text())

_warned = [json.loads(ln[6:]) for ln in _body.splitlines()
           if ln.startswith("data: ") and '"metadata_corrupt"' in ln]
assert _warned, f"必须推出 metadata_corrupt 提示；实际事件：{_body[:400]}"
assert _warned[0]["type"] == "warning"
assert "done" in _body, "⚠️ 损坏只是警告——生成必须照常完成（不阻断）"
print(f"PASS: 出处头损坏 → 推 warning({_warned[0]['kind']}) 且生成照常完成")

# ═══════════════════════════════════════════════════════════════
# 5. 对照：出处头正常 → **不**推 metadata_corrupt（防「一律提示」）
# ═══════════════════════════════════════════════════════════════

with client.stream("GET", "/api/generate/tr_comp/stream?mode=notes&model_id=1") as _rs2:
    _body2 = "".join(_rs2.iter_text())
assert "metadata_corrupt" not in _body2, "正常文档不该被提示来源损坏（假阳性的实现会红）"
assert "done" in _body2
print("PASS: 对照 —— 出处头正常时不推 metadata_corrupt")

# ═══════════════════════════════════════════════════════════════
# 6. _auto_title 跳过 composite（用户起的合并名不得被生成正文顶掉）
# ═══════════════════════════════════════════════════════════════

# ⚠️ **必须走流式端点**：非流式端点的 `want_notes` 分支**从不**调用 `_auto_title`
# （只有 polished 路径与 :346 调；这是既有的不对称，本轮不改、只记录）。
# 若拿非流式端点测，「composite 标题没变」会因为**函数根本没跑**而假绿——
# 正是「断言存在 ≠ 断言有效」的又一形态：对照实验（下面那条普通文档）才看得出
# 函数到底跑没跑。
_TITLE_COMP = "Attention_Is_All_You_Need 等 3 篇对比"   # 含 `_` → 命中 is_auto 的「技术名」判据
_mk_doc("tr_comp2", _TITLE_COMP, _COMP_RAW, SourceType.COMPOSITE)
with client.stream("GET", "/api/generate/tr_comp2/stream?mode=notes&model_id=1") as _rs6:
    _ = "".join(_rs6.iter_text())
_title_now = _STORE.get_transcript("tr_comp2").get("title")
assert _title_now == _TITLE_COMP, \
    f"合成文档的标题是用户给的合并名，不该被覆盖；实际变成 {_title_now!r}"

# 对照：普通文档走同一端点，自动标题**照旧生效**（证明这条路径真的会改标题）
_mk_doc("tr_auto", "Auto_Title_Doc", "普通正文", SourceType.WEBPAGE)
with client.stream("GET", "/api/generate/tr_auto/stream?mode=notes&model_id=1") as _rs7:
    _ = "".join(_rs7.iter_text())
assert _STORE.get_transcript("tr_auto").get("title") == "生成的笔记", \
    "普通文档的自动标题行为不该被改动（条件写宽的实现会让它失效）"
print("PASS: composite 跳过 _auto_title；对照 —— 普通文档自动标题照旧生效")

# ═══════════════════════════════════════════════════════════════
# 7. xlsx → 单产物 + prompt 走**数据摘要**（P0-04 stream-bypass-promptbuilder）
#
# 缺陷原貌：流式端点**内联**调 `build_notes_prompt`（不像非流式那样经
# `PromptBuilder`），而后者按 `source_type in DOC_SOURCE_TYPES` 分派 —— **xlsx 在
# 那个集合里**（它确实算文档类输入），于是 Excel 落进通用的 `_DOC_NOTES_ROLE`，
# 「规模/字段/样品」整套数据摘要角色全丢；同时 `mode=both` 还会真跑一次 polished
# （非流式那边 `build_both` 对 xlsx 返回 `(sp, sp)` 后被生成侧拦下，只跑一次）。
# 两条路径对**同一个 source_type** 分派不同，而产物都长得像模像样（模式 #54）。
#
# ⚠️ 判据必须点**角色文案**而不是「有没有产出」：「文档阅读助手」（_DOC_NOTES_ROLE）
#    与「数据分析师」（_EXCEL_ROLE）互斥，改回内联的实现必红。
# ═══════════════════════════════════════════════════════════════

_mk_doc("tr_xlsx", "季度销量表",
        "## Sheet: 销量\n\n月份,销量\n1月,100\n2月,120\n3月,90", SourceType.XLSX)

# ── 7a：非流式 xlsx 同样收成单产物（两条路径同源）──
#
# ⚠️ 这条**不是**「既有行为不变」——本轮改的正是它。改前非流式走 `generate_both`，
# 那边为满足双产物接口返回 `(sp, sp)` 两份**内容相同**的 View → 落两份盘，而第二份
# 前端根本够不到（`tabRegistry` 的 polished 展开源 ALL_TEXT 不含 xlsx）。
# ⚠️ 产物数那条断言在闸门只写 `_is_composite` 时**实测红过**（拿到
# `['notes', 'polished']`）—— 不是推演，是它把非流式那半漏网抓出来的（模式 #38）。
#
# ⚠️ 7a 与 7b 各守**一份** xlsx 分派实现，谁失效只会红自己那条：
#   7a → `PromptBuilder.build` 的 xlsx 分支（非流式走 PromptBuilder）
#   7b → `build_notes_prompt` 的 xlsx 分支（流式内联调用，不经 PromptBuilder）
#   实测：临时废掉 `build_notes_prompt` 那段，**只有 7b 红**。两份实现都要有测试，
#   否则「某一侧悄悄丢了 Excel 角色」可以带着全绿发布。
_calls.clear()
FakeLLMService.call = _capture
try:
    _rx1 = client.post("/api/generate/tr_xlsx", json={"mode": "both", "model_id": 1})
finally:
    FakeLLMService.call = _orig_call
assert _rx1.status_code == 200, _rx1.text
assert _modes("tr_xlsx") == ["notes"], \
    f"非流式 xlsx 只该产 notes，实际 {_modes('tr_xlsx')}（缺闸门 = 多落一份够不到的盘）"
assert len(_calls) == 1, f"非流式 xlsx 只该发起 1 次调用，实际 {len(_calls)} 次"
assert "数据分析师" in _calls[0], "非流式 xlsx 的 prompt 没走数据摘要角色"
print("PASS: [7a] 非流式 xlsx → 只产 notes + 1 次调用 + prompt 走「数据分析师」")

# ── 7b（核心）：流式 xlsx + mode=both → **只产 notes 且只发起 1 次调用** ──
# 改前：mode=both 走双产物分支 → 2 次 `llm.stream`（其中一次是白烧的 polished）。
_stream_prompts: list[str] = []


def _capture_stream(self, prompt, **kw) -> _FakeStream:
    _stream_prompts.append(prompt)
    return _FakeStream(["# 数据摘要", "\n\n这不是正文。"])   # 注意：产物文本**不是**判据


_orig_stream = FakeLLMService.stream
FakeLLMService.stream = _capture_stream
try:
    with client.stream("GET", "/api/generate/tr_xlsx/stream?mode=both&model_id=1") as _rsx:
        _bx = "".join(_rsx.iter_text())
finally:
    FakeLLMService.stream = _orig_stream

assert "done" in _bx, f"流式请求必须正常完成；实际 {_bx[:300]}"
assert _modes("tr_xlsx") == ["notes"], \
    f"流式 xlsx 只该产 notes，实际 {_modes('tr_xlsx')}（缺闸门的实现会多出 polished）"
assert len(_stream_prompts) == 1, \
    f"xlsx 只该发起 1 次 LLM 调用，实际 {len(_stream_prompts)} 次（缺闸门 = 白烧一次 polished）"
_px = _stream_prompts[0]
assert "数据分析师" in _px, \
    "流式 xlsx 的 prompt 没走数据摘要角色（`build_notes_prompt` 缺 xlsx 分支的实现会红）"
assert "文档阅读助手" not in _px, "流式 xlsx 落进了通用文档角色（_DOC_NOTES_ROLE）"
print("PASS: [7b] 流式 xlsx → 只产 notes + 1 次调用 + prompt 走「数据分析师」")

# ── 7c（对照）：普通网页流式 mode=both → 2 次调用、走**文档角色**（闸门没写宽）──
_stream_prompts.clear()
FakeLLMService.stream = _capture_stream
try:
    with client.stream("GET", "/api/generate/tr_plain/stream?mode=both&model_id=1") as _rsp:
        _bp = "".join(_rsp.iter_text())
finally:
    FakeLLMService.stream = _orig_stream

assert "done" in _bp, f"对照请求必须正常完成；实际 {_bp[:300]}"
assert len(_stream_prompts) == 2, \
    f"对照失败：普通文档 mode=both 必须跑 2 个产物，实际 {len(_stream_prompts)} 次"
assert not any("数据分析师" in p for p in _stream_prompts), \
    "对照失败：普通网页不该被 xlsx 分支截胡（判据写宽的实现会红）"
print("PASS: [7c 对照] 普通网页流式 mode=both 仍跑 2 个产物且不走 Excel 角色")

# ═══════════════════════════════════════════════════════════════
# 8. 双产物单路失败 → **只有失败那一路**标 partial（P1-08）
#
# 缺陷原貌：`status = "partial" if was_cancelled else "complete"` 是**一个**全局变量，
# 同时喂给 notes 与 polished 两路。于是一路失败（另一路成功）时**两路都标 complete**
# —— 包括那个断在半截的（失败前已流出的 chunk 照样落盘）。后果不止是显示：
# 续写入口靠 `md.get("status") == "partial"` 找回残缺产物（本文件 §… 之外，
# generate.py 的 `continue_from` 分支），标成 complete = 用户**没法续写**一条
# 被截断的笔记，而界面上看不出任何异常。
#
# ⚠️ 判据必须是「**两路 status 不相等**」，不能只断「失败那路是 partial」——
# 只断后者的话，一个「一律标 partial」的实现照样全绿（模式 #38：判据要有区分力）。
# ⚠️ 与前端 `onWarning`（GenPanel.tsx:778 置 `genOutcomeRef='partial'`）**不重复**：
# 那是会话内瞬态判断（只决定收尾 toast / onDone 文案、**不落盘**），
# 本条测的是**落盘字段**，两者各管一头。
# ═══════════════════════════════════════════════════════════════


class _FailingStream:
    """先吐一段再抛错 —— 模拟「写了一半断流」。

    ⚠️ **必须先吐一个 chunk 再抛**：完全没吐 chunk 的那路拿不到任何内容 →
    **不落盘** → status 无从观测（那种形态下「两路共用一个 status」也看不出问题）。
    这也是真实世界里唯一能留下 partial 产物的失败形态。
    """

    def __init__(self, chunks, boom: bool = True):
        self._chunks = list(chunks)
        self._boom = boom

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._chunks:
            return self._chunks.pop(0)
        if self._boom:
            self._boom = False
            raise RuntimeError("模拟断流")
        raise StopAsyncIteration

    async def aclose(self):
        self._chunks.clear()


def _fail_polished_stream(self, prompt, **kw) -> _FakeStream:
    """polished 半途断流、notes 正常。

    区分两路的判据是**角色文案**（`_WEB_POLISH_ROLE`「专业的内容编辑」vs
    `_WEB_NOTES_ROLE`「内容阅读助手」），**不是调用顺序** —— 两个产物是并发
    `create_task`，谁先调到 `stream()` 不保证。
    """
    if "专业的内容编辑" in prompt:
        return _FailingStream(["# 整理版（写到这里断了）"])
    return _FakeStream(["# 生成的笔记", "\n\n这篇笔记是完整的。"])


_mk_doc("tr_p18", "单路失败对照", "这是一篇普通网页的正文。", SourceType.WEBPAGE)
FakeLLMService.stream = _fail_polished_stream
try:
    with client.stream("GET", "/api/generate/tr_p18/stream?mode=both&model_id=1") as _rs8:
        _b8 = "".join(_rs8.iter_text())
finally:
    FakeLLMService.stream = _orig_stream

assert "done" in _b8, f"单路失败不该让整条流断掉；实际 {_b8[:400]}"
_md8 = {m["mode"]: m for m in _STORE.get_markdowns_by_transcript("tr_p18")}
assert set(_md8) == {"notes", "polished"}, f"两路都该落盘，实际 {sorted(_md8)}"
assert _md8["notes"]["status"] == "complete", \
    f"notes 那路成功，必须标 complete；实际 {_md8['notes']['status']}"
assert _md8["polished"]["status"] == "partial", \
    ("半截的 polished 必须标 partial（旧的全局 status 实现下它会是 complete，"
     f"且用户再也续写不了它）；实际 {_md8['polished']['status']}")
assert _md8["notes"]["status"] != _md8["polished"]["status"], \
    "两路 status 必须各自独立（相等 = 又变回一个全局 status 喂两路）"
print(f"PASS: 双产物单路失败 → notes={_md8['notes']['status']} / "
      f"polished={_md8['polished']['status']}（逐路判定）")

# ── 对照：两路都成功 → 必须都是 complete（防「一律标 partial」的实现假绿）──
_mk_doc("tr_p18b", "双路成功对照", "这是一篇普通网页的正文。", SourceType.WEBPAGE)
FakeLLMService.stream = _orig_stream
try:
    with client.stream("GET", "/api/generate/tr_p18b/stream?mode=both&model_id=1") as _rs8b:
        _b8b = "".join(_rs8b.iter_text())
finally:
    FakeLLMService.stream = _orig_stream

_md8b = {m["mode"]: m for m in _STORE.get_markdowns_by_transcript("tr_p18b")}
assert set(_md8b) == {"notes", "polished"}, f"两路都该落盘，实际 {sorted(_md8b)}"
assert all(m["status"] == "complete" for m in _md8b.values()), \
    f"完全成功的一次生成，两路都该是 complete；实际 {[(k, v['status']) for k, v in _md8b.items()]}"
print("PASS: [对照] 双路都成功 → 两路都是 complete（判据不是「一律 partial」）")

import shutil
for _t in _TMP:
    shutil.rmtree(_t, ignore_errors=True)

print("\n全部通过：生成闸门（产物裁剪 / 格式清空 / 出处头损坏提示 / 自动标题 / xlsx 单产物 / 逐路 status）")
