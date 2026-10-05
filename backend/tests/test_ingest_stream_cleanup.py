"""SSE 导入的**临时文件生命周期**与**结束信号**（P1-10）。

三个缺口，共同点是「零报错，只在磁盘上悄悄涨」：

① 适配器失败时，上传的临时源文件（可达 2GB）**从不删除** —— 实测在
   `~/.limo/tmp` 里累积了 83 个文件 / 221MB。非流式端点只在「适配器抛异常」
   那两支里删，而「格式不支持」/「无输入」/「persist 抛异常」三条早退路径全漏；
② `_persist_ingest_result` 在 `run_pipeline` 的 try **之外** —— 它抛异常时
   queue 永远收不到 done/error，消费侧只能干等到 900s 静默超时才报
   「处理超时（15分钟无响应）」，而真正的异常连日志都没有；
③ 没有结束哨兵 —— 任何漏网的分支都表现为「15 分钟无响应」。

⚠️ **成功路径的 tmp 不能删**：`_persist_ingest_result` 已把它登记成
`source_file_path`（重新转录要用）。所以本文件的判据是**成对**的
（失败删 / 成功留），只测一半的实现可以靠「一律删」或「一律留」全绿。

⚠️ 所有 SSE 读取都带**硬超时**：本文件要守的正是「失败时流能不能及时结束」，
   没有超时的话实现一旦退化，测试会挂满 900s 才失败 —— 那种红等于没有红。
"""
import json
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.core.errors import AppError
from app.core.types import ProgressEvent, RawMetadata, RawTranscript, SourceType
from app.storage.store import Store

import app.routers.ingest as I

_TMP: list[str] = []


def _new_store() -> Store:
    d = tempfile.mkdtemp()
    _TMP.append(d)
    return Store(d)


_STORE = _new_store()

app = FastAPI()
app.include_router(I.router, prefix="/api")
app.state.store = _STORE
app.state.config = {"data_dir": str(_STORE.root)}
app.state.guard = None


@app.exception_handler(AppError)
async def _app_error_handler(request, exc: AppError):
    return JSONResponse(status_code=200 if exc.recoverable else exc.status_code,
                        content=exc.to_dict())


client = TestClient(app)


# ── 假适配器 ──────────────────────────────────────────────

class _FakeOutput:
    def __init__(self, raw: str):
        self.raw_text = raw
        self.metadata = RawMetadata()
        self.engine = "fake"


class _FakeAdapter:
    """只为驱动路由；真正的适配器行为不在本文件关心范围内。"""

    name = "text"

    def __init__(self, raw: str = "正文", boom: BaseException | None = None):
        self._raw = raw
        self._boom = boom

    async def adapt(self, inp, on_progress=None, engine_config=None):
        if on_progress:
            on_progress(ProgressEvent(phase="parse", status="running", percent=10, message="解析中"))
        if self._boom is not None:
            raise self._boom
        return _FakeOutput(self._raw)


def _patch_route(monkeypatch, adapter) -> None:
    monkeypatch.setattr(I.AdapterRegistry, "route", staticmethod(lambda inp: adapter))


def _tmp_files() -> list[str]:
    d = Path(_STORE.root) / "tmp"
    return sorted(p.name for p in d.iterdir()) if d.is_dir() else []


def _read_stream(path: str, payload: bytes, timeout: float = 10.0) -> tuple[int, str]:
    """POST 一条 SSE 并读到底 —— **带硬超时**（见模块 docstring）。"""
    box: dict = {}

    def _run():
        try:
            with client.stream(
                "POST", path,
                files={"file": ("样例.pdf", payload, "application/pdf")},
            ) as r:
                box["status"] = r.status_code
                box["body"] = "".join(r.iter_text())
        except Exception as e:                      # noqa: BLE001 — 收集后由断言报告
            box["err"] = repr(e)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout)
    assert not t.is_alive(), (
        f"SSE 流 {timeout}s 内没有结束 —— 失败路径没有结束信号，"
        "消费侧会空等到 900s 静默超时（这正是 P1-10 要修的形态）")
    assert "err" not in box, f"读取流时抛异常：{box.get('err')}"
    return box["status"], box["body"]


def _events(body: str) -> list[dict]:
    return [json.loads(ln[6:]) for ln in body.splitlines()
            if ln.startswith("data: ") and ln[6:].strip() != "null"]


# ═══════════════════════════════════════════════════════════
# 1. 适配器失败 → tmp 被清理（非流式端点同样）
# ═══════════════════════════════════════════════════════════

def test_stream_removes_tmp_on_adapter_failure(monkeypatch):
    _patch_route(monkeypatch, _FakeAdapter(boom=RuntimeError("适配器炸了")))
    before = _tmp_files()

    status, body = _read_stream("/api/ingest/stream", b"x" * 1024)
    assert status == 200, status
    errs = [e for e in _events(body) if e.get("type") == "error"]
    assert errs, f"适配器失败必须推 error 事件；实际 {body[:300]}"
    assert _tmp_files() == before, (
        f"失败路径留下了临时文件：{set(_tmp_files()) - set(before)}"
        f"（修复前这里会累积，实测 ~/.limo/tmp 涨到 221MB）")


def test_nonstream_removes_tmp_on_adapter_failure(monkeypatch):
    """非流式端点的同一判据 —— 两条路径的清理收口现在是同一份实现。"""
    _patch_route(monkeypatch, _FakeAdapter(boom=RuntimeError("适配器炸了")))
    before = _tmp_files()

    r = client.post("/api/ingest",
                    files={"file": ("样例.pdf", b"y" * 1024, "application/pdf")})
    # ⚠️ 判 `code` 不判 HTTP 状态：`INGEST_FAILED` 是 recoverable 的，按模式 #36
    #    会返回 **200**（业务失败仍可重试），按状态判会把失败读成成功。
    assert r.json().get("code") == "INGEST_FAILED", f"适配器失败必须报错；实际 {r.status_code} {r.text[:200]}"
    assert _tmp_files() == before, f"失败路径留下了临时文件：{set(_tmp_files()) - set(before)}"


# ═══════════════════════════════════════════════════════════
# 2. 对照：**成功**路径的 tmp 必须保留（它是 source_file_path）
# ═══════════════════════════════════════════════════════════

def test_success_keeps_tmp_as_source_file(monkeypatch):
    """⚠️ 这条是防「一律删」的对照 —— 没有它，把清理写成无条件的实现照样全绿。"""
    payload = f"成功导入的正文 {os.urandom(8).hex()}".encode()
    _patch_route(monkeypatch, _FakeAdapter(raw=payload.decode()))
    before = set(_tmp_files())

    status, body = _read_stream("/api/ingest/stream", payload)
    assert status == 200, status
    done = [e for e in _events(body) if e.get("type") == "done"]
    assert done, f"成功导入必须推 done；实际 {body[:300]}"

    tx_id = done[0]["id"]
    tx = _STORE.get_transcript(tx_id)
    assert tx is not None, "文档必须入库"
    # ⚠️ 字段名是 camelCase `sourceFilePath`（index 条目统一 camelCase）。
    #    写成蛇形会拿到空串 → `Path("")` 是 `.` → `.exists()` 恒真 → **假绿**。
    raw_src = tx.get("sourceFilePath") or ""
    assert raw_src, "成功导入必须登记源文件路径（`sourceFilePath`），否则下面两条断言是空转"
    src = Path(raw_src)
    assert src.exists(), (
        f"成功导入的源文件被删了（{src}）—— 它是「重新转录」的输入，删掉等于砍掉一个既有功能")
    assert src.name in set(_tmp_files()), "该文件的登记路径就是 tmp 里的这一份"


def test_nonstream_success_keeps_tmp(monkeypatch):
    """⚠️ 非流式端点的成功路径**是另一份实现**（finally 在端点函数体里，
    不在 `run_pipeline` 里），必须单独钉住。

    实测教训：只给流式做「一律删」的对照实验时，把非流式那处改坏了本文件
    **依然全绿** —— 两条路径各有各的 finally，一条的用例守不住另一条。
    """
    payload = f"非流式成功 {os.urandom(8).hex()}".encode()
    _patch_route(monkeypatch, _FakeAdapter(raw=payload.decode()))

    r = client.post("/api/ingest",
                    files={"file": ("样例2.pdf", payload, "application/pdf")})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("status") == "done", f"导入应成功；实际 {data}"

    raw_src = (_STORE.get_transcript(data["id"]) or {}).get("sourceFilePath") or ""
    assert raw_src, "成功导入必须登记 `sourceFilePath`"
    assert Path(raw_src).exists(), "非流式成功路径的源文件被删了（它是「重新转录」的输入）"
    assert Path(raw_src).name in set(_tmp_files())


# ═══════════════════════════════════════════════════════════
# 3. `_persist_ingest_result` 抛异常 → 流必须**及时**结束
#
# 修复前：persist 在 try 之外 → 异常逃逸 → queue 永远收不到 done/error →
# 消费者挂到 900s 才报「处理超时」。本条的硬超时就是判据。
# ═══════════════════════════════════════════════════════════

def test_persist_failure_ends_stream_promptly(monkeypatch):
    _patch_route(monkeypatch, _FakeAdapter(raw="persist 阶段炸"))

    async def _boom(*a, **kw):
        raise RuntimeError("persist 炸了")

    monkeypatch.setattr(I, "_persist_ingest_result", _boom)
    before = _tmp_files()

    status, body = _read_stream("/api/ingest/stream", b"z" * 1024, timeout=10.0)
    assert status == 200, status
    errs = [e for e in _events(body) if e.get("type") == "error"]
    assert errs, f"persist 异常必须作为 error 事件送达（修复前它连日志都没有）；实际 {body[:300]}"
    assert _tmp_files() == before, f"persist 失败仍留下临时文件：{set(_tmp_files()) - set(before)}"


# ═══════════════════════════════════════════════════════════
# 4. 结束哨兵 —— 覆盖「except 拦不住的异常」
#
# ⚠️ 用 **BaseException 子类**而不是 RuntimeError：`except Exception` 拦不住它，
#    所以这条**只能**由 `finally` 里的哨兵兜住。拿 Exception 测的话，测到的其实是
#    「except 分支推了 error 事件」，哨兵有没有都绿（判据没有区分力）。
# ═══════════════════════════════════════════════════════════

class _Boom(BaseException):
    """刻意继承 BaseException —— 模拟任何 `except Exception` 覆盖不到的退出路径。"""


def test_end_sentinel_covers_base_exceptions(monkeypatch):
    _patch_route(monkeypatch, _FakeAdapter(boom=_Boom("哨兵用例")))

    # ⚠️ 不带硬超时的失败形态就是「挂 900s」，所以这里的超时是判据本身：
    #    哨兵缺席 → `read_stream` 在 10s 时断言失败。
    status, body = _read_stream("/api/ingest/stream", b"w" * 1024, timeout=10.0)
    assert status == 200, status
    # ⚠️ 只断言「**没有** done/error」——progress 事件是适配器自己在炸之前推的，
    #    它有或没有都不说明哨兵的事。判据是**流结束了**（`_read_stream` 的硬超时），
    #    不是「流里有什么」。
    kinds = {e.get("type") for e in _events(body)}
    assert not (kinds & {"done", "error"}), (
        f"BaseException 逃出了 except Exception，不该有终态事件（但它必须以『流结束』收场）；"
        f"实际 {sorted(kinds)}")


def test_sentinel_is_actually_wired():
    """源码守卫：哨兵与 `data is None` 的消费端必须成对存在。

    行为用例（上一条）能证明「流结束了」，但证明不了是**哪条路径**让它结束的
    （`finally` 里的哨兵 vs 别的地方漏了个 break）。这条把两半钉在源码上。
    """
    src = Path(I.__file__).read_text(encoding="utf-8")
    assert "queue.put_nowait(None)" in src, \
        "`run_pipeline` 的 finally 里缺少结束哨兵（漏了 → 失败路径空等 900s）"
    assert "if data is None:" in src, \
        "消费端没有处理哨兵 —— `data[\"type\"]` 会先抛 TypeError，比原问题更难查"
    # 哨兵必须在 finally 里（在 except 之后），不能只在某条 except 分支里
    _tail = src.split("async def event_generator", 1)[0]
    _finally = _tail.rsplit("finally:", 1)[-1]
    assert "queue.put_nowait(None)" in _finally, \
        "哨兵不在 finally 块里 —— 只能覆盖它所在的那一条分支"


def teardown_module(module):
    for d in _TMP:
        shutil.rmtree(d, ignore_errors=True)


# ═══════════════════════════════════════════════════════════
# 5. 两个端点的上传逻辑必须同源（模式 #54）
# ═══════════════════════════════════════════════════════════

def test_upload_write_path_is_single_source():
    """守卫：`_handle_file_upload` 只能被 `_file_pipeline_input` 调用。

    修复前 `ingest` 与 `ingest_stream` **各有一份逐字同构**的写盘逻辑
    （只差错误类型），改一处漏一处零报错 —— 两边写的还是同一个磁盘路径，
    看起来都「在工作」（模式 #54）。
    """
    import ast

    tree = ast.parse(Path(I.__file__).read_text(encoding="utf-8"))
    owners: set[str] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "_handle_file_upload"):
                owners.add(fn.name)
    assert owners == {"_file_pipeline_input"}, (
        f"写盘逻辑出现多个入口（同源漂移）：{sorted(owners)} —— "
        "上传的写盘 + 大小校验必须只有 `_file_pipeline_input` 一份")
