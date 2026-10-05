"""定制导出的**单一实现**与**不阻塞事件循环**（P1-11 / P1-12）。

两条缺陷同在一处代码上：

· **P1-11** `export_custom`（非流式）与 `_build_export_zip`（SSE 端点用）是同一件事
  的两份实现，且**行为已分叉**：空结果一处 `AppError(EXPORT_NO_CONTENT, 400)`、
  另一处 `return buf, 0` → 照发 done → 用户下载到一个 22 字节的空 zip 并以为导出
  成功了。两份看起来都「在工作」，代价只在其中一份上出现。

· **P1-12** `_build_export_zip` 是 `async def` 但**函数体零 await**，却被
  `await _build_export_zip(...)` 调用（旧 :1459）→ 整个打包（含 KaTeX 服务端渲染的
  `subprocess.run(["node", ...])`，最长 60s）跑在**事件循环线程**上：该端点一被调用，
  同进程所有请求（含 `/health`）全部挂起。它目前无前端调用者，所以不是线上症状，
  而是「任何客户端一调就冻结后端」。

⚠️ 本文件的判据**成对**：光测「空结果报 400」的话，「把 `export_custom` 与 stream 各
   写一份」的实现照样能全绿（那正是修复前的形状）—— 所以另有一组把「只有一份
   ZIP 写入逻辑 / 重活走线程池」钉在源码上的守卫。守卫同时是**收敛的验收**：改回
   两份实现必红。
"""
import ast
import io
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.core.dependencies import get_store
from app.core.errors import AppError
from app.core.types import (GenerateMode, MarkdownView, RawMetadata, RawTranscript,
                            SourceType, Style)
from app.storage.store import Store

import app.routers.export as E

_TMP: list[str] = []


def _new_store() -> Store:
    d = tempfile.mkdtemp()
    _TMP.append(d)
    return Store(d)


_STORE = _new_store()

# 一份「有内容」的文档：notes 产物 + rawText 都非空
_STORE.save_transcript(RawTranscript(
    id='tr_zip001', title='导出样例', source_type=SourceType.WEBPAGE,
    engine='trafilatura', raw_text='# 原文\n\n正文内容', content_hash='hz1',
    metadata=RawMetadata(),
))
_STORE.save_markdown(MarkdownView(
    id='md_zip_notes', transcript_id='tr_zip001',
    content='# 笔记\n\n这是笔记正文',
    style=Style.DETAILED, mode=GenerateMode.NOTES,
))

app = FastAPI()
app.include_router(E.router, prefix="/api")
app.dependency_overrides[get_store] = lambda: _STORE


@app.exception_handler(AppError)
async def _app_error_handler(request, exc: AppError):
    # ⚠️ 必须与 main.py **同规则**：显式 status_code 优先，未显式时才 recoverable → 200
    #    （模式 #36）。漏掉前半句的话，`EXPORT_NO_CONTENT`（显式 400 + recoverable）
    #    会被压成 200 —— 测试自己造出一份比生产更宽松的契约，判据就失去意义。
    http_status = exc.status_code if exc._explicit_status else (
        200 if exc.recoverable else exc.status_code)
    return JSONResponse(status_code=http_status, content=exc.to_dict())


client = TestClient(app)


def _func(tree: ast.AST, name: str):
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name == name:
            return fn
    raise AssertionError(f'export.py 里没有 {name}')


def _zip_writes(fn) -> list[ast.Call]:
    """函数体（含嵌套函数）里所有 `zipfile.ZipFile(...)` 调用。"""
    return [n for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == 'ZipFile'
            and isinstance(n.func.value, ast.Name) and n.func.value.id == 'zipfile']


def _zip_entries(buf: io.BytesIO) -> list[str]:
    with zipfile.ZipFile(buf) as zf:
        return zf.namelist()


# ═══════════════════════════════════════════════════════════
# 1. 行为：空结果 → EXPORT_NO_CONTENT + 400（不是空 zip）
# ═══════════════════════════════════════════════════════════

def test_empty_result_raises_400_not_empty_zip():
    """⚠️ 判据是「**报错**」而不是「返回了个东西」：修复前另一条实现返回 `(buf, 0)`
    并照发 done —— 用户拿到一个 22 字节的 zip，解压后空无一物，且全程零报错。
    """
    r = client.post('/api/export/custom', json={
        'doc_ids': ['tr_不存在'], 'content_tabs': ['summary'], 'formats': ['md'],
    })
    # ⚠️ 断言 `code` 与 **HTTP 400** 两条：`EXPORT_NO_CONTENT` 是 `recoverable=True`，
    #    不给显式 status_code 时会按 AppError 默认返回 200（模式 #36），前端 `!r.ok`
    #    于是把失败读成成功、照样去解那个空 zip。
    assert r.status_code == 400, f"空结果必须 400；实际 {r.status_code} {r.text[:200]}"
    assert r.json().get('code') == 'EXPORT_NO_CONTENT', r.text[:200]


def test_nonempty_export_has_files():
    """对照：有内容时正常产出（防止「一律报错」也能过上面的用例）。"""
    r = client.post('/api/export/custom', json={
        'doc_ids': ['tr_zip001'], 'content_tabs': ['summary'], 'formats': ['md'],
    })
    assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
    entries = _zip_entries(io.BytesIO(r.content))
    assert len(entries) == 1, entries
    assert entries[0].endswith('_summary.md'), entries
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert '这是笔记正文' in zf.read(entries[0]).decode('utf-8')


# ═══════════════════════════════════════════════════════════
# 2. 源码守卫：只有一份 ZIP 写入逻辑（P1-11 的验收）
# ═══════════════════════════════════════════════════════════

def test_only_one_zip_writer_for_custom_export():
    """**定制导出只能有一份 ZIP 写入逻辑**（P1-11 的验收条件）。

    判据落在「谁不许写 ZIP」上，不落在「全文件只能写几次」上：`export.py` 里另有
    `export_all_products` / `export_batch` / `export_conversations` 三个 ZIP 端点，
    那是**别的功能**（各有各的产物流），不在本条射程内。

    改回两份实现的形状（例如把打包逻辑内联回 `export_custom`）必红。
    """
    tree = ast.parse(Path(E.__file__).read_text(encoding='utf-8'))
    assert _zip_writes(_func(tree, '_build_export_zip')), \
        '`_build_export_zip` 里没有 ZIP 写入 —— 收敛过头了，它才是唯一的实现'
    for wrapper in ('export_custom', 'export_custom_stream'):
        assert not _zip_writes(_func(tree, wrapper)), (
            f'{wrapper} 里出现了 ZIP 写入 —— 定制导出又长出了第二份实现（P1-11）；'
            '它只该调 `_build_export_zip`')


# ═══════════════════════════════════════════════════════════
# 3. 源码守卫：打包走线程池，不占事件循环（P1-12 的验收）
# ═══════════════════════════════════════════════════════════

def test_build_zip_is_plain_sync_function():
    """`_build_export_zip` 必须是**同步** def，且体内零 `await`。

    ⚠️ 这条守的正是 P1-12 那个「看不出来」的形态：`async def` + 零 await 读起来
    完全正常，只有把它 `await` 的那一刻才知道代价（独占事件循环）。同步 def 则
    **无法**被直接 await —— 谁想把它放回事件循环，必须先改这里、也就必然看到本
    注释指向的代价。
    """
    tree = ast.parse(Path(E.__file__).read_text(encoding='utf-8'))
    fn = _func(tree, '_build_export_zip')
    assert isinstance(fn, ast.FunctionDef), \
        '_build_export_zip 被改成了 async def —— 它体内是纯同步的 CPU/IO（含 60s 的 node 子进程）'
    awaits = [n for n in ast.walk(fn) if isinstance(n, (ast.Await, ast.AsyncFor, ast.AsyncWith))]
    assert not awaits, '同步函数里出现了 await —— 说明它又被改回 async 了'


def test_stream_runs_build_in_thread_with_threadsafe_push():
    """SSE 端点必须 `to_thread` 调它，且进度回调**跨线程**回到 loop。"""
    src = Path(E.__file__).read_text(encoding='utf-8')
    assert 'asyncio.to_thread(' in src, \
        'SSE 端点没有把打包丢进线程池 —— 会独占事件循环（P1-12）'
    assert 'call_soon_threadsafe' in src, (
        '进度回调没有跨线程回到 loop —— worker 线程里直接操作 asyncio.Queue '
        '只在非 debug 模式下「碰巧能用」（call_soon 的线程检查默认关闭）')

    tree = ast.parse(src)
    run_build = _func(tree, 'run_build')
    calls = [n.func.attr for n in ast.walk(run_build)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
    assert 'to_thread' in calls, f'run_build 里没有走 to_thread：{calls}'


def test_endpoint_is_thin_wrapper_over_single_impl():
    """`export_custom` 端点直接复用 `_build_export_zip`（薄包装）。"""
    tree = ast.parse(Path(E.__file__).read_text(encoding='utf-8'))
    fn = _func(tree, 'export_custom')
    assert isinstance(fn, ast.FunctionDef), \
        'export_custom 必须是同步 def（FastAPI 才会把它丢进线程池）'
    called = [n.func.id for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert '_build_export_zip' in called, \
        f'export_custom 没有复用 `_build_export_zip`（又长出第二份实现）：{called}'


def teardown_module(module):
    for d in _TMP:
        shutil.rmtree(d, ignore_errors=True)
