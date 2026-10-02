"""验证合成端点 POST /api/synthesis/stream — SSE 协议 / 业务错误走事件 / 槽位释放

⚠️ **不发起真实计费调用**：`LLMService` 被模块级替换为假类（stuff 路径本就不调 LLM）。

⚠️ 本文件的核心纪律：**业务失败必须是 SSE 事件，不能是 HTTP 错误**。前端拿
`fetch` + reader 解这条流，中途换成 HTTP JSON 会让它解不出来（用户看到的是
「连接中断」而不是「请至少选 2 篇」）。所以每条「X 失败」的断言都同时钉住
①事件存在 ②`type == "error"` ③错误码正确。
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.concurrency import ExecutionGuard
from app.core.types import RawMetadata, RawTranscript, SourceType
from app.storage.store import Store

import app.routers.synthesis as S

_TMP: list[str] = []


def _new_store() -> Store:
    d = tempfile.mkdtemp()
    _TMP.append(d)
    return Store(d)


class FakeLLMService:
    """最小替身：端点只需 `api_key` 与 `aclose()`；stuff 路径不调 LLM。

    ⚠️ `calls` 是**类级**的：端点每次请求都新建一个实例，记在实例上等于每次
    从零开始——「窗口不足时零调用」的断言会恒真（新实例的 calls 永远是空的）。
    """

    calls: list[str] = []

    def __init__(self, config, model_id=None, request_timeout=None):
        self.provider = "dashscope"
        self.model = "qwen-max"
        self.api_key = "sk-fake-key-for-tests"
        self.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        self.capabilities = ["text"]
        self.closed = False

    async def call(self, prompt, **kw) -> str:
        FakeLLMService.calls.append(prompt)
        return "研究问题：测试\n方法：无"

    async def aclose(self) -> None:
        self.closed = True


_STORE = _new_store()
_CFG = {
    "llm": {"provider": "dashscope", "model": {"dashscope": "qwen-max"},
            "api_key": "sk-fake-key-for-tests",
            "base_url": {"dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1"}},
    "target_language": "zh-CN",
    # ⚠️ 两个模型是刻意的：id=1 装得下（stuff），id=2 窗口不足（触发 multi 闸门）。
    # 留空 enabled_models 会让两者都落到保守 32k —— 那时"窗口不足"用例会绿，
    # 但绿在错误的理由上（不是我们给的小窗口，而是兜底值）。
    "enabled_models": [
        {"id": 1, "provider_id": "dashscope", "model_name": "qwen-max",
         "context_window": 1_000_000, "capabilities": ["text"]},
        {"id": 2, "provider_id": "dashscope", "model_name": "small",
         "context_window": 32_768, "capabilities": ["text"]},
    ],
    "data_dir": str(_STORE.root),
}

app = FastAPI()
app.include_router(S.router, prefix="/api")
app.state.store = _STORE
app.state.config = _CFG
app.state.guard = ExecutionGuard(cloud_max=5, local_max=2)

S.LLMService = FakeLLMService
client = TestClient(app)


def _mk_doc(tx_id: str, title: str, raw: str) -> None:
    _STORE.save_transcript(RawTranscript(
        id=tx_id, title=title, source_type=SourceType.PDF, engine="test",
        raw_text=raw, content_hash="", metadata=RawMetadata(), tags=[]),
        skip_dedup=True)


def _sse(body: str) -> list[dict]:
    """解析 SSE 正文里的 data 事件（`: heartbeat` 注释行自动忽略）。"""
    out = []
    for ln in body.splitlines():
        if ln.startswith("data: "):
            out.append(json.loads(ln[6:]))
    return out


def _synth(payload: dict) -> tuple[int, list[dict], str]:
    with client.stream("POST", "/api/synthesis/stream", json=payload) as r:
        body = "".join(r.iter_text())
    return r.status_code, _sse(body), body


_mk_doc("tr_a", "论文甲", "甲的研究问题与方法。" * 40)
_mk_doc("tr_b", "论文乙", "乙的研究问题与方法。" * 40)

# ═══════════════════════════════════════════════════════════════
# 1. 正常路径：2 篇装得下 → stage + done，且真的落库成 composite
# ═══════════════════════════════════════════════════════════════

_code, _ev, _body = _synth({"doc_ids": ["tr_a", "tr_b"], "title": "甲乙对比", "model_id": 1})
assert _code == 200, _code
_types = [e["type"] for e in _ev]
assert "stage" in _types, f"长任务必须推 stage（否则用户只看到静止进度条）；实际 {_types}"
assert "done" in _types, f"正常路径必须推 done；实际事件：{_ev}"
assert "error" not in _types, f"正常路径不该有 error；实际 {_ev}"

_done = next(e for e in _ev if e["type"] == "done")
_new_id = _done["id"]
_tx = _STORE.get_transcript(_new_id)
assert _tx is not None, f"done 给的 id {_new_id} 必须能在库里查到"
assert _tx.get("sourceType") == "composite", f"新建文档必须是 composite；实际 {_tx.get('sourceType')}"
assert _tx.get("title") == "甲乙对比", f"用户给的合并名要落库；实际 {_tx.get('title')!r}"
assert "## 来源文档" in _tx.get("rawText", ""), "出处（来源清单）必须写进正文本身"
assert "【文档 1】" in _tx["rawText"] and "【文档 2】" in _tx["rawText"], \
    "两篇都要有边界标记（缺一篇则模型分不清哪些文字属于谁）"
assert _done["mode"] == "stuff", f"2 篇小文档装得下，应走 stuff；实际 {_done['mode']}"
# ⚠️ 列表接口直出 index，是「N 合 1」徽章的数据来源——不在这一层加字段前端就没数据
_listed = {t["id"]: t for t in _STORE.list_transcripts()}
assert _listed[_new_id].get("sourceDocIds") == ["tr_a", "tr_b"], \
    f"list_transcripts 必须带出 sourceDocIds；实际 {_listed[_new_id].get('sourceDocIds')}"
print(f"PASS: 正常路径 → stuff / stage+done / 落库 composite（{_new_id}）+ 来源清单")

# ═══════════════════════════════════════════════════════════════
# 2. 篇数不足 → **SSE error 事件**（不是 HTTP 错误）
# ═══════════════════════════════════════════════════════════════

_code2, _ev2, _body2 = _synth({"doc_ids": ["tr_a"], "model_id": 1})
assert _code2 == 200, f"业务失败必须仍是 200 + SSE（前端按 SSE 解析）；实际 HTTP {_code2}"
_errs = [e for e in _ev2 if e["type"] == "error"]
assert _errs, f"1 篇必须给 error 事件；实际 {_ev2}"
assert _errs[0].get("code") == "SYNTH_TOO_FEW_SOURCES", _errs[0]
assert _errs[0].get("message"), "错误必须带面向用户的文案"
assert "done" not in [e["type"] for e in _ev2], "失败路径不该推 done"
print(f"PASS: 1 篇 → SSE error({_errs[0]['code']})，HTTP 仍 200")

# ═══════════════════════════════════════════════════════════════
# 3. 源文档已删 → error(SYNTH_SOURCE_MISSING)
# ═══════════════════════════════════════════════════════════════

_code3, _ev3, _ = _synth({"doc_ids": ["tr_a", "tr_not_exist"], "model_id": 1})
_e3 = [e for e in _ev3 if e["type"] == "error"]
assert _e3 and _e3[0]["code"] == "SYNTH_SOURCE_MISSING", f"实际 {_ev3}"
print(f"PASS: 源已删 → SSE error({_e3[0]['code']})")

# ═══════════════════════════════════════════════════════════════
# 4. 模型窗口不足 → error(SYNTH_WINDOW_TOO_SMALL)，且**零 LLM 调用**
# ═══════════════════════════════════════════════════════════════

_calls_before = len(FakeLLMService.calls)
_code4, _ev4, _ = _synth({"doc_ids": ["tr_a", "tr_b"], "model_id": 2})
_e4 = [e for e in _ev4 if e["type"] == "error"]
assert _e4 and _e4[0]["code"] == "SYNTH_WINDOW_TOO_SMALL", f"实际 {_ev4}"
assert "窗口" in _e4[0]["message"] or "32768" in _e4[0]["message"], \
    f"文案要能让人知道是窗口问题：{_e4[0]['message']}"
assert len(FakeLLMService.calls) == _calls_before, \
    "窗口不足必须在**任何 LLM 调用之前**拒绝（先白跑一次探路调用再拒绝 = 白花钱）"
print(f"PASS: 窗口 32k → SSE error({_e4[0]['code']})，闸门在最前")

# ═══════════════════════════════════════════════════════════════
# 5. 未配 LLM Key → error(SYNTH_NO_API_KEY)
# ═══════════════════════════════════════════════════════════════

_orig_init = FakeLLMService.__init__


def _no_key_init(self, config, model_id=None, request_timeout=None):
    _orig_init(self, config, model_id, request_timeout)
    self.api_key = ""


FakeLLMService.__init__ = _no_key_init
try:
    _code5, _ev5, _ = _synth({"doc_ids": ["tr_a", "tr_b"], "model_id": 1})
finally:
    FakeLLMService.__init__ = _orig_init
_e5 = [e for e in _ev5 if e["type"] == "error"]
assert _e5 and _e5[0]["code"] == "SYNTH_NO_API_KEY", f"实际 {_ev5}"
print(f"PASS: 缺 Key → SSE error({_e5[0]['code']})")

# ═══════════════════════════════════════════════════════════════
# 6. 信号量：每次请求后必须归还（否则几次之后云端槽位永久耗尽）
# ═══════════════════════════════════════════════════════════════

assert app.state.guard.semaphore._value == 5, \
    f"连续 5 次请求（含失败路径）后槽位必须全部归还；实际剩 {app.state.guard.semaphore._value}"
print("PASS: 五种结局（成功/篇数不足/源已删/窗口不足/缺Key）后信号量均归还")

# ═══════════════════════════════════════════════════════════════
# 7. ⚠️ 哨兵：runner 抛出**未被捕获**的异常时，客户端立刻收尾（不干等静默超时）
# ═══════════════════════════════════════════════════════════════

# 这是本端点唯一会在「没有 done/error 事件」的情况下结束的路径。若哪天有人
# 把 runner 的 `except Exception` 删掉、或抛出的不是 Exception 子类，
# 没有哨兵的话主循环要等 SYNTH_QUIET_TIMEOUT（900s）——用户对着进度条干等 15 分钟。
# 断言分两层：①能**立刻**结束 ②明确没有 done。
#
# ⚠️ **对照实验的表现形式很特别，这里如实记下**：把哨兵删掉后，测试不是
# 「断言失败」而是**整个挂死**（180s 被外部 timeout 杀掉）。因为缺哨兵时流
# 根本不会结束——它每 30s 发一次心跳、永远等不到 done/error，`iter_text()`
# 也就永远读不完。耗时断言（下面那 20s）在那种实现下**根本执行不到**。
# 结论不变（哨兵是必需的），但排查时别去等一条红字断言：没有红字、只有不返回，
# 本身就是「缺哨兵」的签名。
_orig_synth = S.synthesize


async def _boom(*a, **kw):
    raise asyncio.CancelledError("模拟未被 except Exception 捕获的中断")


S.synthesize = _boom
try:
    _t0 = time.monotonic()
    _code7, _ev7, _body7 = _synth({"doc_ids": ["tr_a", "tr_b"], "model_id": 1})
    _elapsed = time.monotonic() - _t0
finally:
    S.synthesize = _orig_synth
assert _code7 == 200, _code7
assert "done" not in [e["type"] for e in _ev7], \
    f"任务异常结束却推了 done —— 前端会把它当成功；实际 {_ev7}"
# ⚠️ **耗时判据是这里唯一的区分力**：删掉哨兵时主循环最终也会被静默超时兜底
# （同样是 error、同样无 done），只断事件类型根本看不出来。缺哨兵的实现最快要
# 30s（第一次心跳 wait_for 超时）——20s 是宽松阈值：正常路径 <1s，CI 再慢也不会误报。
assert _elapsed < 20, \
    f"runner 异常必须**立刻**收尾；实际耗时 {_elapsed:.1f}s（哨兵缺失时会干等超时兜底）"
assert app.state.guard.semaphore._value == 5, "异常路径同样必须归还槽位"
print(f"PASS: 未捕获异常 → 哨兵立即收尾（{_elapsed:.2f}s、不误报成功、归还槽位）")

for _t in _TMP:
    shutil.rmtree(_t, ignore_errors=True)

print("\n全部通过：合成端点（SSE 协议 / 业务错误走事件 / 槽位释放 / 异常哨兵）")
