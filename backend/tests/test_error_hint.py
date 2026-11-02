"""错误的「可行动性」：`AppError.hint` 与生成侧结构化 error 事件。

三条判据，各自对应一个**看起来等效、实则不同**的实现：

① `to_dict()` 在 `hint` 为空时**不含该键**（而不是恒输出 `"hint": ""`）——
   没填 hint 的错误码响应必须与改造前**逐字节相同**，否则「有没有 hint」
   不再是从响应形状上可判的事实，前端也没法据此决定要不要渲染那一行。

② `_sse_error_line()` 把 `code` / `recoverable` / `hint` / `detail` 带出去
   （而不是只发 `{type, message}`）。

③ **生成侧真的走通了**：流内抛 `AppError` 时，SSE 里能读到 `code` 与 `hint`。
   这条是 ② 的**端到端对照**——只测 ② 的话，`except AppError` 排在
   `except Exception` 之后（或干脆不写）时单测照样全绿，而线上永远拿不到 hint
   （`AppError.__init__` 末尾的 `super().__init__(message)` 让 `str(e)` 只剩 message）。

⚠️ **端到端只覆盖单产物路径（`mode=notes`），这是有意的**：双产物路径里每个产物
由 `_run_product` 单独兜住，它的 `except Exception` 把失败降级成 per-product 的
`warning` 事件（P0-9 的语义——一路失败不该清空另一路）。那里的 `AppError` **不该**
冒到外层，与「结构化 error」不是同一个场景，别把它当成漏测。

⚠️ **本文件是 pytest 形态**（`def test_*`）：顶层裸断言在 `pytest tests/ -q` 下
收集 0 个、静默空跑。
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.core.concurrency import ExecutionGuard
from app.core.errors import AppError, http_status
from app.core.types import RawMetadata, RawTranscript, SourceType
from app.storage.store import Store

import app.routers.generate as G

_TMP: list[str] = []


@pytest.fixture(scope="module", autouse=True)
def _cleanup():
    yield
    for d in _TMP:
        shutil.rmtree(d, ignore_errors=True)


# ── 假 LLM ───────────────────────────────────────────────────────────────────

class FakeLLMService:
    """最小替身：构造要成功（端点会先过 `GEN_NO_API_KEY` 闸门），`stream` 按需抛错。"""

    #: 类属性（每次请求都新建实例，记在实例上等于每次从零开始）
    stream_error: AppError | None = None
    api_key: str = "sk-fake-key-for-tests"

    def __init__(self, config, model_id=None, request_timeout=None):
        self.provider = "dashscope"
        self.model = "qwen-max"
        self.api_key = FakeLLMService.api_key
        self.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        self.capabilities = ["text"]
        self.supports_reasoning = False

    def stream(self, prompt, **kw):
        if FakeLLMService.stream_error is not None:
            raise FakeLLMService.stream_error
        raise AssertionError("本文件的用例都应在 stream 里抛错，不该走到这里")


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
    # ⚠️ 走生产同一份 `http_status()`，不复刻表达式（模式 #54：测试与线上状态码
    # 不同时不会抛错，只会静默判错）。
    return JSONResponse(status_code=http_status(exc), content=exc.to_dict())


client = TestClient(app)


async def _idle_watch_disconnect(request, cancel_event, interval: float = 0.5) -> None:
    """顶替 `G._watch_disconnect`：什么也不做，只等着被 cancel。

    ⚠️ **这是测试环境的取舍，不是产品修复**。真实实现反复调 `request.is_disconnected()`，
    而 TestClient 的 `receive()` 是 `anyio.to_thread.run_sync(queue.get)`；在
    `StreamingResponse` 已经起了自己的 `listen_for_disconnect` 之后，这条路径会挂死
    （实测：watcher 第 6 次 `is_disconnected()` 不返回，`task.cancel()` 也唤不醒它，
    `asyncio.wait_for(task, 3)` 直接超时）。对照实验：把同样的 watcher 单独放进一个
    最小 FastAPI app 里跑 11 轮 + cancel，**全程正常** —— 说明触发条件与
    generate.py 的完整状态有关，而不是「TestClient 不能调 is_disconnected」本身。

    本文件测的是「AppError 的 code/hint 到不到得了客户端」，断连监听不是被测对象；
    而它在 uvicorn 下走的是可重入的 ASGI 消息队列，行为与 TestClient 不同。
    因此把它换成一个不会挂的替身，**而不是**给产品代码加一层超时兜底
    （那会掩盖真正的 task 泄漏，且 `_watch_disconnect` 是既有代码，生产上没出过问题）。
    """
    await asyncio.sleep(3600)


@pytest.fixture(autouse=True)
def _reset_fake(monkeypatch):
    """`G.LLMService` 是**模块级共享全局**，替换必须在这里做 —— 多个测试文件都在 import
    时替换它，pytest 收集阶段「最后 import 的赢」（详见 `test_artifact_followup.py`
    文件头的同一段说明）。"""
    monkeypatch.setattr(G, "LLMService", FakeLLMService)
    monkeypatch.setattr(G, "_watch_disconnect", _idle_watch_disconnect)
    FakeLLMService.stream_error = None
    FakeLLMService.api_key = "sk-fake-key-for-tests"
    yield


def _mk_doc(tx_id: str, title: str, raw: str,
            stype: SourceType = SourceType.WEBPAGE) -> None:
    _STORE.save_transcript(RawTranscript(
        id=tx_id, title=title, source_type=stype, engine="test",
        raw_text=raw, content_hash="", metadata=RawMetadata(), tags=[]),
        skip_dedup=True)


def _sse(body: str) -> list[dict]:
    return [json.loads(ln[6:]) for ln in body.splitlines() if ln.startswith("data: ")]


# ── ① to_dict() 的键集 ──────────────────────────────────────────────────────

def test_未填_hint_时_to_dict_不含该键():
    d = AppError("SOME_CODE", "出事了").to_dict()
    assert "hint" not in d, "没填 hint 的响应必须与改造前逐字节相同"
    assert d["code"] == "SOME_CODE" and d["message"] == "出事了"


def test_填了_hint_时_to_dict_带上它():
    d = AppError("SOME_CODE", "出事了", hint="去设置里看看").to_dict()
    assert d["hint"] == "去设置里看看"


def test_str_只有_message_所以结构化字段必须显式带走():
    """这条钉的是**根因**：`AppError.__init__` 末尾 `super().__init__(message)` 让
    `str(e)` 只剩 message —— 这就是 catch-all 的 `str(e)[:200]` 会把 code /
    recoverable / hint 全部丢掉的原因。"""
    e = AppError("GEN_X", "人话文案", hint="下一步", recoverable=False)
    assert str(e) == "人话文案"


# ── ② _sse_error_line() 的载荷 ──────────────────────────────────────────────

def test_sse_error_line_带全部结构化字段():
    line = G._sse_error_line(AppError(
        "GEN_X", "人话文案", detail="upstream 503",
        recoverable=False, hint="换个模型试试"))
    assert line.startswith("data: ") and line.endswith("\n\n")
    payload = json.loads(line[len("data: "):])
    assert payload == {
        "type": "error", "message": "人话文案", "code": "GEN_X",
        "recoverable": False, "hint": "换个模型试试", "detail": "upstream 503",
    }


def test_sse_error_line_空字段不出现():
    payload = json.loads(G._sse_error_line(AppError("GEN_X", "人话文案"))[len("data: "):])
    assert payload == {"type": "error", "message": "人话文案", "code": "GEN_X",
                       "recoverable": True}


# ── ③ 生成侧端到端 ─────────────────────────────────────────────────────────

def test_流内_AppError_的_hint_真的到得了客户端():
    """单产物路径（`mode=notes`）里 `llm.stream` 抛 AppError → 外层 `except AppError`
    → 结构化 SSE。

    **对照实验**：把 `except AppError` 删掉、或挪到 `except Exception` 之后，
    这里只会收到 `{"type": "error", "message": "模型说它今天不想工作"}`，
    `code` / `hint` 双双消失 —— push 断言会红。
    """
    _mk_doc("tx_hint", "带 hint 的失败", "这是足够长的正文内容，用来通过预处理。")
    FakeLLMService.stream_error = AppError(
        "GEN_FAKE_FAIL", "模型说它今天不想工作",
        detail="upstream 503", hint="换个模型试试", recoverable=True)

    with client.stream("GET", "/api/generate/tx_hint/stream?mode=notes") as r:
        body = "".join(r.iter_text())

    errors = [e for e in _sse(body) if e.get("type") == "error"]
    assert len(errors) == 1, f"期望恰好一条 error 事件，实际：{errors}"
    ev = errors[0]
    assert ev["code"] == "GEN_FAKE_FAIL"
    assert ev["message"] == "模型说它今天不想工作"
    assert ev["hint"] == "换个模型试试"
    assert ev["detail"] == "upstream 503"
    assert ev["recoverable"] is True


def test_流内无_hint_的_AppError_不多带键():
    """零回归的一半：没填 hint 的错误，SSE 形状与「只加结构化字段」之前一致 ——
    多出 `"hint": ""` 会让「有没有 hint」不再可判。"""
    _mk_doc("tx_nohint", "没 hint 的失败", "这是足够长的正文内容，用来通过预处理。")
    FakeLLMService.stream_error = AppError("GEN_FAKE_FAIL", "模型说它今天不想工作")

    with client.stream("GET", "/api/generate/tx_nohint/stream?mode=notes") as r:
        body = "".join(r.iter_text())

    errors = [e for e in _sse(body) if e.get("type") == "error"]
    assert len(errors) == 1
    assert errors[0] == {"type": "error", "message": "模型说它今天不想工作",
                         "code": "GEN_FAKE_FAIL", "recoverable": True}


# ── 入口段（不是 SSE，是 JSON 错误体）──────────────────────────────────────

def test_入口段_GEN_NOT_FOUND_带_hint_且是_JSON_不是_SSE():
    """入口段跑在 `StreamingResponse` 构造之前 —— 那里只能 `raise`（写 `yield` 会让
    `generate_stream` 变成 async generator，末尾 `return StreamingResponse` 当场
    SyntaxError）。所以客户端拿到的是 JSON 错误体，不是 SSE。"""
    r = client.get("/api/generate/tx_does_not_exist/stream?mode=notes")
    assert r.status_code == 200, "recoverable 的 AppError 压成 200（模式 #36）"
    assert "text/event-stream" not in r.headers.get("content-type", "")
    body = r.json()
    assert body["code"] == "GEN_NOT_FOUND"
    assert body["hint"], "入口段这条是补 hint 的重点之一（用户不知道该去哪）"


def test_入口段_GEN_NO_API_KEY_不可重试且带_hint():
    _mk_doc("tx_nokey", "缺 Key", "这是足够长的正文内容，用来通过预处理。")
    FakeLLMService.api_key = ""

    r = client.get("/api/generate/tx_nokey/stream?mode=notes")
    assert r.status_code == 400, "recoverable=False → 默认 400"
    body = r.json()
    assert body["code"] == "GEN_NO_API_KEY"
    assert body["recoverable"] is False
    assert "设置" in body["hint"], "hint 要指路到用户能动手的地方"
