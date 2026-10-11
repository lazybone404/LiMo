"""定制导出链的三条判据：tab 适用性 / 空结果语义 / 标记剥离

2026-10-11 的代码审查在「详细导出」上查出四条缺陷，本文件逐条钉住。四条的共同形态
都是**看起来成功**：

  ① `content_tabs` 是前端**按类型分组后拍平的并集**，后端却把它套到每一篇上 ——
     不适用的 tab 从前会兜底成 `rawText`，于是网页文档凭空多出 `_提取文字.md`、
     图片文档多出 `_笔记.md`，内容是同一段原文（实跑：2 篇 → 9 个文件，6 个是重复原文）。
  ② `/export/batch` 对「全部 id 都无产物」照发 22 字节空 zip + HTTP 200，而**同一操作
     只选 1 篇**走 `/export/{id}/all` 会报 404 —— 「1 篇报错、2 篇静默成功」。
  ③ `_collect_doc_products`（一键导出的唯一实现）不剥 `limo-block` 标记，而定制导出剥
     —— 同一条「预览所见 = 导出所得」在两条链上给出相反结果。
  ④ `/export/{id}/all` 的空结果检查是**参照物**，本文件把它与 ② 并排断，防止哪天
     两边又被改成不同语义。

⚠️ 每条的对照实验写在用例 docstring 里：**按那个改法改回去，本条必红**。

运行：python -m pytest tests/test_export_tab_applicability.py
"""
import io
import os
import sys
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.core.dependencies import get_store
from app.core.errors import AppError, http_status
from app.core.types import (GenerateMode, MarkdownView, ProcessResult, RawMetadata,
                            RawTranscript, SourceType, Style)
from app.routers import export
from app.routers.export import _collect_doc_products, _resolve_tab_content
from app.storage.store import Store

#: 带 `limo-block` 标记的笔记正文 —— 标记行在预览里不显示，导出必须与之一致
NOTES_WITH_MARKER = (
    '# 网页甲\n\n'
    '<!-- limo-block:v1 type="key-takeaway" -->\n'
    '**这是重点**\n'
    '<!-- /limo-block -->\n\n'
    '结尾段落\n'
)
OCR_TEXT = '# 图片乙\n\n这是 OCR 出来的字\n'


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """独立 app（不 import main）+ 全新临时库；两篇**类型不同**的文档。"""
    s = Store(str(tmp_path_factory.mktemp("export_tabs")))

    s.save_transcript(RawTranscript(
        id='tr_web', title='网页甲', source_type=SourceType.WEBPAGE,
        engine='trafilatura', raw_text='网页的原始正文', content_hash='h_web',
        metadata=RawMetadata()))
    s.save_markdown(MarkdownView(
        id='md_web', transcript_id='tr_web', content=NOTES_WITH_MARKER,
        style=Style.DETAILED, mode=GenerateMode.NOTES))

    s.save_transcript(RawTranscript(
        id='tr_img', title='图片乙', source_type=SourceType.IMAGE,
        engine='upload', raw_text='图片的 rawText', content_hash='h_img',
        metadata=RawMetadata()))
    s.save_process_result(ProcessResult(
        id='pr_extract', transcript_id='tr_img', mode='extract',
        engine='dashscope-ocr', content=OCR_TEXT))

    app = FastAPI()
    app.include_router(export.router, prefix='/api')
    app.dependency_overrides[get_store] = lambda: s

    @app.exception_handler(AppError)
    async def _err_handler(request, exc: AppError):
        return JSONResponse(status_code=http_status(exc), content=exc.to_dict())

    yield TestClient(app), s


def _zip_names(resp) -> set[str]:
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        return set(zf.namelist())


def _custom(client, **over) -> set[str]:
    body = {
        'doc_ids': ['tr_web', 'tr_img'],
        # ⚠️ 这就是前端实际发出的形状：**各类型 tab 的并集**（网页有 summary，
        # 图片有 extract，拼在一起谁都不同时拥有这两者）。
        'content_tabs': ['summary', 'extract'],
        'formats': ['md'],
        'image_md': 'none',
    }
    body.update(over)
    r = client.post('/api/export/custom', json=body)
    assert r.status_code == 200, r.text
    return _zip_names(r)


# ── ① tab 适用性 ──

def test_resolve_tab_empty_for_inapplicable_tab(client):
    """没跑过处理的 tab、没产出的 mode，一律返回空串（= 不产文件）。

    ⚠️ 对照实验：把 `_resolve_tab_content` 的 else 分支改回「找不到就返回 rawText」
    → 本条立刻红（会返回『图片的 rawText』而不是空串）。
    """
    _, s = client
    tx_web = s.get_transcript('tr_web')
    tx_img = s.get_transcript('tr_img')
    mds_web = s.get_markdowns_by_transcript('tr_web')
    mds_img = s.get_markdowns_by_transcript('tr_img')

    # 网页文档没有 process result → extract 不适用
    assert _resolve_tab_content(s, tx_web, mds_web, 'extract') == ''
    # 图片文档没有 markdown 产物 → summary 不适用
    assert _resolve_tab_content(s, tx_img, mds_img, 'summary') == ''
    # 图片文档没有处理结果的两个 tab 也不适用（前端已不发，这里守后端兜底）
    assert _resolve_tab_content(s, tx_img, mds_img, 'original') == ''
    assert _resolve_tab_content(s, tx_img, mds_img, 'ocr') == ''

    # 反面：适用的一侧仍要正常取到内容（否则「全部返回空」也能让上面全绿）
    assert '这是重点' in _resolve_tab_content(s, tx_web, mds_web, 'summary')
    assert 'OCR 出来的字' in _resolve_tab_content(s, tx_img, mds_img, 'extract')


def test_custom_export_produces_no_phantom_files(client):
    """端到端：并集 tab 套到每篇上，**不得**产出名字对不上的假文件。

    ⚠️ 对照实验：改回 else 的 rawText 回退 → 本条红（zip 里会多出
    `网页甲/网页甲_extract.md` 与 `图片乙/图片乙_summary.md`，内容都是 rawText）。
    """
    names = _custom(client[0])
    assert '网页甲/网页甲_summary.md' in names, '网页文档该有的笔记没导出'
    assert '图片乙/图片乙_extract.md' in names, '图片文档该有的 OCR 结果没导出'
    # 这两条是假产物的具名形式 —— 断言「不在」比断言「总数」更能说清是什么错了
    assert '网页甲/网页甲_extract.md' not in names, '网页文档凭空多出了「提取文字」'
    assert '图片乙/图片乙_summary.md' not in names, '图片文档凭空多出了「笔记」'
    assert len(names) == 2, f'只应有 2 个文件，实际 {sorted(names)}'


def test_custom_export_strips_block_markers(client):
    """定制导出的 md 是干净的（标注留）—— 与一键导出同源。"""
    r = client[0].post('/api/export/custom', json={
        'doc_ids': ['tr_web'], 'content_tabs': ['summary'],
        'formats': ['md'], 'image_md': 'none'})
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        md = zf.read('网页甲/网页甲_summary.md').decode('utf-8')
    assert 'limo-block' not in md, '定制导出的 md 残留了协议标记'
    assert '这是重点' in md, '剥标记不该连块内文字一起剥掉'


# ── ② / ④ 空结果语义：两条链必须**同款** ──

def test_batch_empty_result_is_404_not_empty_zip(client):
    """`/export/batch` 全部 id 都无产物 → 404，**不发空 zip**。

    ⚠️ 对照实验：删掉 `export_batch` 里那段 `if total_files == 0: raise` →
    本条红（返回 200 + 22 字节空 zip）。
    """
    c, _ = client
    r = c.post('/api/export/batch', json={'ids': ['tr_not_exist', 'tr_also_missing']})
    assert r.status_code == 404, f'空结果应显式报错，实际 {r.status_code}'
    assert r.json().get('code') == 'EXPORT_NO_CONTENT'


def test_all_and_batch_agree_on_empty_result(client):
    """**同一个「没东西可导」在两条链上给同一个答案** —— 本用例的真正对象是「成对」。

    ⚠️ 这正是 ② 被发现时的形态：`/all` 早就报错，`/batch` 静默成功，
    而 `_build_export_zip` 的注释当时写着「空结果已统一」。
    """
    c, _ = client
    r_all = c.get('/api/export/tr_not_exist/all')
    r_batch = c.post('/api/export/batch', json={'ids': ['tr_not_exist']})
    assert r_all.status_code == r_batch.status_code == 404, (
        f'/all={r_all.status_code} /batch={r_batch.status_code} —— 两处判据又分叉了')


def test_batch_with_valid_ids_still_works(client):
    """反面：有内容时当然要出 zip（否则「一律 404」也能让上面两条绿）。"""
    c, _ = client
    r = c.post('/api/export/batch', json={'ids': ['tr_web', 'tr_img']})
    assert r.status_code == 200
    names = _zip_names(r)
    assert any('网页甲' in n for n in names)
    assert any('图片乙' in n for n in names)


# ── ③ 一键导出的 md 剥标记 ──

def test_collect_doc_products_strips_block_markers(client):
    """`_collect_doc_products` 是「一键导出所有产物」的唯一实现，它也要剥标记。

    ⚠️ 对照实验：把 `_strip_blocks(md["content"])` 改回 `md["content"]` → 本条红。
    ⚠️ 这条**必须**与 `test_custom_export_strips_block_markers` 同时存在：
    两条链各测一次，才守得住「同源」——只测一边时，另一边被改回原样没有任何提示。
    """
    _, s = client
    products = _collect_doc_products(s, 'tr_web')
    notes = [v for k, v in products.items() if '笔记' in k]
    assert notes, f'没找到笔记产物，实际键 {sorted(products)}'
    assert 'limo-block' not in notes[0], '一键导出的 md 残留了协议标记'
    assert '这是重点' in notes[0], '剥标记不该把块内文字一起剥掉'


def test_all_products_zip_has_clean_markdown(client):
    """同上，但从**真正的入口**再走一遍（单测过函数 ≠ 端点上真的这么跑）。"""
    c, _ = client
    r = c.get('/api/export/tr_web/all')
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        mds = [n for n in zf.namelist() if n.endswith('.md')]
        assert mds, f'zip 里没有 md 产物：{zf.namelist()}'
        for n in mds:
            body = zf.read(n).decode('utf-8')
            assert 'limo-block' not in body, f'{n} 残留了协议标记'
