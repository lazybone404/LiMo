"""产物自证头（`<!-- limo-meta {json} -->`）在**四种导出格式**里的去留。

判据只有一句话，但它分岔在两条链上，所以四条都要钉：

| 格式 | 自证头 | 理由 |
|---|---|---|
| `.md` | 保留 | 有源码可留 —— 产物离开 `.limo` 之后靠那一行自己说明来历 |
| `.html` | 保留 | 渲染时 HTML 注释不可见，读者看不到它 |
| `.docx` | 保留在源、渲染时剥 | `docx_export` 剥 bs4 的 `Comment` 节点（纯渲染格式没有源码可留） |
| `.txt` | **剥离** | 纯文本没有「注释」这层语义 —— 打开就是一行 `<!-- limo-meta {…} -->` |

⚠️ 这一处最容易出的错是**只改一半**：给纯文本加了剥离，却顺手把 `.md` 也剥了
（或者反过来，给 `_strip_blocks` 里塞进 `strip_meta` —— 那个函数被合成原料、
对话导出、字数统计共用，塞进去会污染非导出链路）。故本文件把四种格式**成对**断言，
断言的是「哪一档有、哪一档没有」，不是单独一句「纯文本里没有」。

⚠️ `.txt` 只出现在定制导出（`_build_export_zip`）的 `fmt == "plain"` 那一支；
`GET /export/{id}` 的 `format` 只认 md / html / docx。夹具因此分两套：定制导出走
`/api/export/custom` 拿 ZIP，单文档导出走 `GET /api/export/{id}`。
"""
from __future__ import annotations

import io
import shutil
import tempfile
import zipfile

from docx import Document
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.core.dependencies import get_store
from app.core.errors import AppError
from app.core.types import (GenerateMode, MarkdownView, RawMetadata, RawTranscript,
                            SourceType, Style)
from app.routers import export as E
from app.storage.store import Store

_TMP: list[str] = []


def _new_store() -> Store:
    d = tempfile.mkdtemp()
    _TMP.append(d)
    return Store(d)


_STORE = _new_store()

#: 正文首行刻意不复述「limo-meta」这个词 —— 否则「导出里没有 limo-meta」这条断言
#: 会因为正文自己带着这个词而永远绿或永远红，与自证头无关。
BODY = "# 笔记\n\n这是笔记正文，用来确认剥离只动头、不动正文。\n"

#: 自证头的形状取真实产物那一套（`generation/provenance.py::artifact_meta`）；
#: `dump_meta` 会自己补 `v`，这里不必写。
FILE_META = {
    "type": "artifact",
    "kind": "notes",
    "source": {"sourceType": "webpage", "engine": "trafilatura"},
    "generated": {"generatedAt": "2026-11-07T10:00:00", "model": "qwen-max",
                  "promptVersion": "22"},
    "identity": {},
    "trust": {"textSource": "model"},
}

# 两篇：`tr_meta01` 带自证头（主夹具）；`tr_meta02` 不带（零回归对照）
_STORE.save_transcript(RawTranscript(
    id='tr_meta01', title='带头样例', source_type=SourceType.WEBPAGE,
    engine='trafilatura', raw_text='# 原文\n\n正文', content_hash='meta1',
    metadata=RawMetadata(),
))
_STORE.save_markdown(MarkdownView(
    id='md_meta01', transcript_id='tr_meta01', content=BODY,
    style=Style.DETAILED, mode=GenerateMode.NOTES,
), file_meta=FILE_META)

_STORE.save_transcript(RawTranscript(
    id='tr_meta02', title='无头样例', source_type=SourceType.WEBPAGE,
    engine='trafilatura', raw_text='# 原文\n\n正文', content_hash='meta2',
    metadata=RawMetadata(),
))
_STORE.save_markdown(MarkdownView(
    id='md_meta02', transcript_id='tr_meta02', content=BODY,
    style=Style.DETAILED, mode=GenerateMode.NOTES,
))

app = FastAPI()
app.include_router(E.router, prefix="/api")
app.dependency_overrides[get_store] = lambda: _STORE


@app.exception_handler(AppError)
async def _app_error_handler(request, exc: AppError):
    # ⚠️ 与 main.py 同规则（模式 #36）：显式 status_code 优先，未显式才 recoverable→200。
    http_status = exc.status_code if exc._explicit_status else (
        200 if exc.recoverable else exc.status_code)
    return JSONResponse(status_code=http_status, content=exc.to_dict())


client = TestClient(app)


def _custom(fmt: str, doc: str = 'tr_meta01') -> bytes:
    """跑一次定制导出，返回 ZIP 里唯一那个文件的字节。"""
    r = client.post('/api/export/custom', json={
        'doc_ids': [doc], 'content_tabs': ['summary'], 'formats': [fmt],
    })
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = zf.namelist()
        assert len(names) == 1, names
        return zf.read(names[0])


# ═══════════════════════════════════════════════════════════════
# 夹具自检：产物文件真的带着自证头（否则下面所有「没有」都是同义反复）
# ═══════════════════════════════════════════════════════════════

def test_夹具产物真的带自证头():
    on_disk = _STORE.get_markdowns_by_transcript('tr_meta01', mode='notes')[0]['content']
    assert on_disk.startswith('<!-- limo-meta'), on_disk[:80]
    assert 'limo-meta' not in _STORE.get_markdowns_by_transcript('tr_meta02', mode='notes')[0]['content']


# ═══════════════════════════════════════════════════════════════
# 定制导出（`_build_export_zip`）：四格式去留
# ═══════════════════════════════════════════════════════════════

def test_纯文本剥掉自证头_正文一字不少():
    out = _custom('plain').decode('utf-8')
    assert 'limo-meta' not in out, f"纯文本里还留着自证头：{out[:120]!r}"
    # 对照：剥的是头，不是整段正文 —— 只断「没有头」的话，「返回空文件」也能过
    assert '这是笔记正文' in out, out[:200]
    assert out.startswith('# 笔记'), f"头剥完后正文应顶到第一行：{out[:80]!r}"


def test_md_保留自证头():
    out = _custom('md').decode('utf-8')
    # ⚠️ 对照（有区分力）：把 `strip_meta` 塞进 `_strip_blocks`（看着更省事）→ 这条红。
    # 那个函数被合成原料 / 对话导出 / 字数统计共用，塞进去会污染全部非导出链路。
    assert out.startswith('<!-- limo-meta'), out[:80]
    assert '这是笔记正文' in out


def test_html_保留自证头在源码里():
    out = _custom('html').decode('utf-8')
    # 注释在页面上不可见，但源码里必须在 —— 与 md 同一条「让产物自己说明来历」。
    assert 'limo-meta' in out
    assert '这是笔记正文' in out


def test_docx_渲染时不出现自证头文字():
    """Word 是纯渲染格式：头留在源里、由 `docx_export` 剥掉注释节点。

    ⚠️ 判据落在**读回的文字**上，不是 docx 字节里搜字符串 —— 字节是压缩的，
    搜不到也证明不了什么（那样这条永远绿）。
    """
    doc = Document(io.BytesIO(_custom('docx')))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert 'limo-meta' not in text, text[:200]
    assert '这是笔记正文' in text


# ═══════════════════════════════════════════════════════════════
# 零回归：没有自证头的产物，四种格式都逐字节不受影响
# ═══════════════════════════════════════════════════════════════

def test_无自证头时纯文本原样输出():
    """`strip_meta` 对无头文本原样返回 —— 新加的这一步不能改动老产物一个字节。"""
    out = _custom('plain', doc='tr_meta02').decode('utf-8')
    assert out.startswith('# 笔记'), out[:80]
    assert '这是笔记正文' in out


# ═══════════════════════════════════════════════════════════════
# 单文档导出端点（`GET /export/{id}`）：md 保留
# ═══════════════════════════════════════════════════════════════

def test_单文档导出_md_保留自证头():
    r = client.get('/api/export/tr_meta01', params={'format': 'md', 'mode': 'notes'})
    assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
    text = r.content.decode('utf-8')
    assert text.startswith('<!-- limo-meta'), text[:80]
    assert '这是笔记正文' in text


def test_单文档导出_html_保留自证头在源码里():
    r = client.get('/api/export/tr_meta01', params={'format': 'html', 'mode': 'notes'})
    assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
    text = r.content.decode('utf-8')
    assert 'limo-meta' in text
    assert '这是笔记正文' in text


def teardown_module(module):
    for d in _TMP:
        shutil.rmtree(d, ignore_errors=True)
