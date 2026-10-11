"""MD 导出把图片一起带走 —— `GET /export/{tx}?format=md&image_md=inline`

**为什么需要它**：快捷「下载 MD」从前只有纯前端一条路（直接把 store 里的正文落盘），
正文里的图片引用是 `/api/assets/{id}/xxx.png` 这种只在本应用内有效的路径 —— 文件一
离开应用就整篇裂图。2026-10-11 加了 `image_md=inline`，让后端把图片 base64 内嵌进
`.md`，**单文件自包含**。

⚠️ 反面实现在多数用例下不报错：忽略 `image_md` 参数、永远走「改写绝对 URL」那条路，
照样 200、照样一份格式正确的 md —— 只是图片全裂。故本文件每条都**断言正文里有没有
base64**，不只看状态码。

⚠️ 另一半同样重要：**默认值必须零回归**（不传 `image_md` 的老调用方拿到的必须与
改造前逐字一致，即「绝对 URL 改写」而不是 base64）。故两条路径的断言**成对**写。

⚠️ 三个文档**各占一个 transcript**：产物是「最新版」语义，共用一个文档时后写入的用例
会改掉前一个用例读到的内容（这类串味只在用例顺序变化时才现形）。

运行：python -m pytest tests/test_export_md_images.py
"""
import base64 as _b64
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.core.dependencies import get_store
from app.core.errors import AppError, http_status
from app.core.types import (GenerateMode, MarkdownView, RawMetadata, RawTranscript,
                            SourceType, Style)
from app.routers import export
from app.storage.store import Store

#: 内容无关紧要 —— `_inline_images` 按**扩展名**给 mime，不解析图片本身。
PNG_BYTES = b'\x89PNG\r\n\x1a\n' + b'FAKE-IMAGE-PAYLOAD'
#: 越界目标的内容，必须**不出现在任何导出物里**
SECRET_TEXT = b'SECRET-MUST-NOT-LEAK'


def _add_doc(s: Store, tx_id: str, title: str, md_content: str) -> None:
    s.save_transcript(RawTranscript(
        id=tx_id, title=title, source_type=SourceType.WEBPAGE,
        engine='trafilatura', raw_text='转录原文', content_hash=f'h_{tx_id}',
        metadata=RawMetadata(),
    ))
    s.save_markdown(MarkdownView(
        id=f'md_{tx_id}', transcript_id=tx_id, content=md_content,
        style=Style.DETAILED, mode=GenerateMode.NOTES))


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """独立 app（不 import main，避开其模块级副作用）+ 全新临时库。"""
    s = Store(str(tmp_path_factory.mktemp("md_img_export")))
    _add_doc(s, 'tr_img', '带图文档', '# 标题\n\n![配图](assets/pic.png)\n\n正文段落\n')
    _add_doc(s, 'tr_ghost', '缺图文档', '# 甲\n\n![缺](assets/ghost.png)\n')
    _add_doc(s, 'tr_esc', '越界文档', '# 乙\n\n![越界](assets/../secret.txt)\n')

    # 文档目录里放一张**合法**图片；`tr_ghost` 引用的文件**刻意不建**；
    # 越界目标（`assets/../secret.txt` 解析后落在 doc_dir 内、但不在 assets 内）
    doc_dir = s.get_doc_dir('tr_img')
    (doc_dir / 'assets').mkdir(parents=True, exist_ok=True)
    (doc_dir / 'assets' / 'pic.png').write_bytes(PNG_BYTES)
    (doc_dir / 'secret.txt').write_bytes(SECRET_TEXT)

    app = FastAPI()
    app.include_router(export.router, prefix='/api')
    app.dependency_overrides[get_store] = lambda: s

    @app.exception_handler(AppError)
    async def _err_handler(request, exc: AppError):
        return JSONResponse(status_code=http_status(exc), content=exc.to_dict())

    yield TestClient(app), s


def _export(client, tx_id: str, fmt: str = 'md', **params) -> str:
    q = '&'.join(f'{k}={v}' for k, v in params.items())
    url = f'/api/export/{tx_id}?format={fmt}&mode=notes' + (f'&{q}' if q else '')
    r = client.get(url)
    assert r.status_code == 200, r.text
    return r.text


# ── 1. 默认（不传 image_md）＝ 改造前的行为，逐字零回归 ──

def test_default_rewrites_to_absolute_url_without_base64(client):
    """**零回归守卫**：不带 `image_md` 时仍然是「改写成绝对 URL」。

    ⚠️ 对照实验：把 `image_md` 的默认值从 `"link"` 改成 `"inline"` → 本条立刻变红。
    """
    c, _ = client
    body = _export(c, 'tr_img')
    assert '/api/assets/tr_img/pic.png' in body, '图片引用没有被改写成绝对 URL'
    assert 'data:image/png;base64,' not in body, '默认路径不该内嵌图片'


# ── 2. inline：图片真的进了文件，且原引用被替换掉 ──

def test_inline_embeds_base64_and_removes_ref(client):
    """`image_md=inline` 时正文里必须出现 base64 data URI。

    ⚠️ 对照实验：去掉 `export_content` md 分支里那段 `if image_md == "inline":` → 本条变红
    （第一半断「图没进来」，第二半断「原引用还在」——少了第二半，一个「既内嵌又保留
    原引用」的实现也能绿，而那会让每张图在渲染器里显示两遍）。
    """
    c, _ = client
    body = _export(c, 'tr_img', image_md='inline')
    assert 'data:image/png;base64,' in body, '图片没有被内嵌'
    assert _b64.b64encode(PNG_BYTES).decode()[:24] in body, '内嵌的不是那张图的字节'
    assert '(assets/pic.png)' not in body, '原始相对引用应当已被替换'
    assert '/api/assets/tr_img/pic.png' not in body, '内嵌后不该再出现指向后端的绝对 URL'


def test_inline_content_matches_source_body(client):
    """内嵌只应动图片引用，正文一个字都不能少。"""
    c, _ = client
    body = _export(c, 'tr_img', image_md='inline')
    assert '# 标题' in body
    assert '正文段落' in body


# ── 3. 找不到 / 越界：不崩、不泄漏，退回原样 ──

def test_missing_image_falls_back_to_absolute_url(client):
    """引用在、文件不在 → 不崩，该条引用退回绝对 URL。

    `_inline_images` 对「没拿到数据」的引用**原样保留**，正好被后面那四条绝对 URL
    改写兜底 —— 这个顺序（先内联、后改写）是本用例钉住的不变量。对照实验：把内联挪到
    改写**之后** → 本条那条缺失引用会停在 `assets/ghost.png`，断言变红。
    """
    c, _ = client
    body = _export(c, 'tr_ghost', image_md='inline')
    assert 'ghost.png' in body, '缺失引用的原文本应当还在（不该被整段吞掉）'
    assert '/api/assets/tr_ghost/ghost.png' in body, '缺失引用应当退回绝对 URL 形态'


def test_traversal_ref_is_not_embedded(client):
    """越界引用**不得**被内嵌 —— 这条通道会把读到的文件 base64 交出去。

    ⚠️ 守卫在 `_collect_images` 里（`resolve_within`），本用例是从**新入口**再验一次：
    新加的 `image_md=inline` 分支只要绕开 `_collect_images` 自己读盘，就会同时得到
    「越界可读」与「文件被交出去」两个后果。
    """
    c, _ = client
    body = _export(c, 'tr_esc', image_md='inline')
    assert SECRET_TEXT.decode() not in body, '越界文件内容泄漏进了导出物'
    assert _b64.b64encode(SECRET_TEXT).decode() not in body, '越界文件被 base64 内嵌'


# ── 4. 该参数只管 md：其它格式不受影响 ──

def test_image_md_only_affects_md_format(client):
    """format=html 时行为与从前一致（HTML 本来就内嵌图，且不看这个参数）。

    守的是「新参数别长成第二个开关」：HTML / docx 的内联条件由各自分支决定，
    不该被这个**只为 md 加**的参数牵动。
    """
    c, _ = client
    body = _export(c, 'tr_img', fmt='html', image_md='inline')
    assert 'data:image/png;base64,' in body       # HTML 本来就会内嵌
    assert '/api/assets/tr_img/pic.png' not in body
