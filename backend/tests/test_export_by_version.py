"""按版本导出 — `GET /export/{tx}?mode=notes&version=N`

守的是**所见即所得**：用户在预览区切到 v1 再导出，拿到的必须是 v1。

⚠️ **反面实现在任何用例下都不报错**：忽略 `version` 直接取 `mds[0]`，照样 200、
照样一份内容完整、格式正确的文件——只是拿错了版本，而文件已经落盘，用户不会发现。
故本文件每条都断言**正文内容**，不只看状态码。

⚠️ 另一半是「显式版本查不到**不回退**最新版」：这是写文件的操作，同
`QueryService.get_artifact` 的纪律（`docs/patterns.md` 里「不静默回退」一族）。

⚠️ 状态码走 `errors.http_status` 而非本地复刻表达式——复刻版漏掉 `_explicit_status`
时会与生产分叉（分叉不抛错，只是静默判错，模式 #54）。
"""
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.core.dependencies import get_store
from app.core.errors import AppError, http_status
from app.core.types import (MarkdownView, RawMetadata, RawTranscript,
                            SourceType, Style, GenerateMode)
from app.routers import export
from app.storage.store import Store


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """独立 app（不 import main，避开其模块级副作用）+ 全新临时库。"""
    s = Store(str(tmp_path_factory.mktemp("ver_export")))
    s.save_transcript(RawTranscript(
        id='tr_ver', title='版本导出', source_type=SourceType.WEBPAGE,
        engine='trafilatura', raw_text='转录原文内容', content_hash='hv1',
        metadata=RawMetadata(),
    ))
    for _i, _body in enumerate(['第一版正文AAA', '第二版正文BBB', '第三版正文CCC'], 1):
        s.save_markdown(MarkdownView(
            id=f'md_v{_i}', transcript_id='tr_ver', content=f'# 标题\n\n{_body}',
            style=Style.DETAILED, mode=GenerateMode.NOTES))
    # polished 也来两版 —— 用来验「mode 过滤 + version 过滤」不会互相串位
    for _i, _body in enumerate(['整理一版EEE', '整理二版FFF'], 1):
        s.save_markdown(MarkdownView(
            id=f'md_p{_i}', transcript_id='tr_ver', content=f'# 标题\n\n{_body}',
            style=Style.DETAILED, mode=GenerateMode.POLISHED))

    app = FastAPI()
    app.include_router(export.router, prefix='/api')
    app.dependency_overrides[get_store] = lambda: s

    @app.exception_handler(AppError)
    async def _err_handler(request, exc: AppError):
        return JSONResponse(status_code=http_status(exc), content=exc.to_dict())

    yield TestClient(app), s


def _export(client, **params) -> str:
    q = '&'.join(f'{k}={v}' for k, v in params.items())
    r = client.get(f'/api/export/tr_ver?{q}')
    assert r.status_code == 200, f'{q} → {r.status_code}: {r.text[:200]}'
    return r.text


# ═══════════════════════════════════════════════════════════════
# 1. 默认（不传 version）= 最新版
# ═══════════════════════════════════════════════════════════════

def test_default_is_latest(client):
    body = _export(client[0], mode='notes', format='md')
    assert '第三版正文CCC' in body, "默认应导最新版"
    assert '第一版正文AAA' not in body, "默认不该混入旧版"


# ═══════════════════════════════════════════════════════════════
# 2. 显式 version → 精确导出那一版
# ═══════════════════════════════════════════════════════════════

def test_explicit_version(client):
    c = client[0]
    v1 = _export(c, mode='notes', format='md', version=1)
    assert '第一版正文AAA' in v1, f"version=1 必须导出第 1 版，实得: {v1[:200]}"
    assert '第二版正文BBB' not in v1 and '第三版正文CCC' not in v1, "不得混入其他版本"

    v2 = _export(c, mode='notes', format='md', version=2)
    assert '第二版正文BBB' in v2 and '第一版正文AAA' not in v2

    # ⚠️ 顺序陷阱：若实现写成 `mds[0]` 而不看 version，上面两条**必有一条**会红；
    #    若写成「取 <= version 的最新」则两条都红。
    assert v1 != v2, "不同版本导出结果必须不同（这是本条测试存在的理由）"


def test_mode_and_version_are_independent_knobs(client):
    # ⚠️ mode 与 version 是**两个独立旋钮**，不能互相串位：polished/v2 与 notes/v2
    #    是同号不同物。实现若把 mode 过滤丢掉、只按 version 取，这里必红。
    p2 = _export(client[0], mode='polished', format='md', version=2)
    assert '整理二版FFF' in p2 and '第二版正文BBB' not in p2, p2[:200]


# ═══════════════════════════════════════════════════════════════
# 3. 显式版本查不到 → 显式报错，**不回退最新版**
# ═══════════════════════════════════════════════════════════════

def test_missing_version_does_not_fall_back(client):
    c = client[0]
    r = c.get('/api/export/tr_ver?mode=notes&format=md&version=99')
    assert r.status_code == 404, f"不存在的版本应 404，实得 {r.status_code}: {r.text[:200]}"
    assert r.json()['code'] == 'EXPORT_VERSION_NOT_FOUND', r.text
    # ⚠️ 断言「正文不在里面」而非只看状态码：一个回退到最新版的实现也可能同时返回 404
    #    以外的错误——真正要钉死的是**别把别的内容当第 99 版发出去**。
    assert '正文' not in r.text, "失败响应里不该夹带任何版本的正文"

    # ⚠️ 也不能回退到**另一份产物**（notes 没有 v99 就去拿 polished 的）：同样是答非所问。
    r = c.get('/api/export/tr_ver?format=md&version=99')       # mode 留空
    assert r.status_code == 404 and r.json()['code'] == 'EXPORT_VERSION_NOT_FOUND', r.text


# ═══════════════════════════════════════════════════════════════
# 4. transcript 无版本概念：传 version 忽略，仍拿到转录原文
# ═══════════════════════════════════════════════════════════════

def test_transcript_ignores_version(client):
    tx_body = _export(client[0], mode='transcript', format='md', version=3)
    assert '转录原文内容' in tx_body, tx_body[:200]


# ═══════════════════════════════════════════════════════════════
# 5. HTML 路径同样按版本（两条分支共用同一段内容选择，需各验一次）
# ═══════════════════════════════════════════════════════════════

def test_html_path_honors_version(client):
    h1 = _export(client[0], mode='notes', format='html', version=1)
    assert '第一版正文AAA' in h1 and '第三版正文CCC' not in h1, h1[:300]


# ═══════════════════════════════════════════════════════════════
# 6. 批量导出（`/export/{tx}/all`）保留**每一个**版本
# ═══════════════════════════════════════════════════════════════

def test_batch_export_keeps_every_version(client):
    s = client[1]
    products = export._collect_doc_products(s, 'tr_ver')
    notes_files = sorted(k for k in products if '笔记' in k)
    assert len(notes_files) == 3, f"3 个版本都该导出，实得 {notes_files}"
    contents = ' '.join(products[k] for k in notes_files)
    for _expect in ('第一版正文AAA', '第二版正文BBB', '第三版正文CCC'):
        assert _expect in contents, f"批量导出漏了 {_expect}: {notes_files}"
