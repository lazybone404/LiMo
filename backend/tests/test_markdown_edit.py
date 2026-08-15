"""验证产物编辑路由 — PUT /api/markdown/{id}：edited 标记 + URL 反转 + style 保留 + 校验"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.core.dependencies import get_store
from app.core.errors import AppError
from app.core.types import RawTranscript, SourceType, RawMetadata, MarkdownView, Style, GenerateMode
from app.storage.store import Store
from app.routers import markdown_edit

# ── 测试 app：只挂 markdown_edit 路由 + override get_store（不触发主 app startup）──
d = tempfile.mkdtemp()
s = Store(d)
app = FastAPI()
app.include_router(markdown_edit.router, prefix="/api")
app.dependency_overrides[get_store] = lambda: s


# main.py 的 AppError handler 在这里复制（测试 app 不挂主 app，需自行注册）
@app.exception_handler(AppError)
async def app_error_handler(request, exc: AppError):
    http_status = 200 if exc.recoverable else exc.status_code
    return JSONResponse(status_code=http_status, content=exc.to_dict())


client = TestClient(app)

# ── 准备：文档 + 已有生成产物（style=detailed，含 assets 图片引用）──
s.save_transcript(RawTranscript(
    id='tr_edit001', title='Edit Doc', source_type=SourceType.WEBPAGE,
    engine='trafilatura', raw_text='原文', content_hash='h1', metadata=RawMetadata(),
))
s.save_markdown(MarkdownView(
    id='md_old_notes', transcript_id='tr_edit001',
    content='# 笔记\n生成内容 ![图](assets/a.png)',
    style=Style.DETAILED, mode=GenerateMode.NOTES,
))

# ── 1. 编辑保存成功：edited=true + version+1 + style 保留 + 返回仍绝对 URL ──
r = client.put('/api/markdown/tr_edit001', json={
    'mode': 'notes',
    'content': '# 笔记（手动改）\n修正后的内容 ![图](/api/assets/tr_edit001/a.png)',
})
assert r.status_code == 200, f"PUT should 200, got {r.status_code}: {r.text}"
data = r.json()
assert data['edited'] is True
assert data['mode'] == 'notes'
assert data['version'] == 2
assert data['style'] == 'detailed', "编辑保存应保留原 style"
assert '修正后的内容' in data['content']
assert '/api/assets/tr_edit001/a.png' in data['content'], "返回 content 应仍是绝对 URL（前端渲染用）"
assert [v['version'] for v in data['versions']] == [2, 1], "versions 应降序"
print('PASS: 编辑保存成功 + edited + version + style 保留')

# ── 2. URL 反转：磁盘落盘为 assets/ 相对路径 ──
entry = s.get_markdowns_by_transcript('tr_edit001', mode='notes')[0]
assert entry['version'] == 2
assert 'assets/a.png' in entry['content'], "落盘应为 assets/ 相对路径"
assert '/api/assets/' not in entry['content'], "绝对 URL 不应写回磁盘"
print('PASS: 图片 URL 反转（绝对 → assets 相对）')

# ── 3. prev 保留：旧生成版可读回 ──
mds = s.get_markdowns_by_transcript('tr_edit001', mode='notes')
assert len(mds) == 2, "两版制：编辑后应有 2 版"
prev_v1 = next(m for m in mds if m['version'] == 1)
assert '生成内容' in prev_v1['content'], "prev 应保留生成版内容"
print('PASS: prev 保留生成版')

# ── 4. polished 也可编辑 ──
s.save_markdown(MarkdownView(id='md_old_polished', transcript_id='tr_edit001',
                             content='整理版', style=Style.MINIMAL, mode=GenerateMode.POLISHED))
r = client.put('/api/markdown/tr_edit001', json={'mode': 'polished', 'content': '整理版（手动）'})
assert r.status_code == 200
p = r.json()
assert p['edited'] is True and p['version'] == 2 and p['mode'] == 'polished'
print('PASS: polished 编辑')

# ── 5. mode 非法 → MD_EDIT_BAD_MODE ──
for bad in ('summary', 'fact_check', 'transcript'):
    r = client.put('/api/markdown/tr_edit001', json={'mode': bad, 'content': 'x'})
    assert r.status_code == 400 and r.json()['code'] == 'MD_EDIT_BAD_MODE', f"{bad} 应被拒绝"
print('PASS: 非法 mode 拒绝')

# ── 6. 文档不存在 → MD_EDIT_NOT_FOUND ──
r = client.put('/api/markdown/tr_edit999', json={'mode': 'notes', 'content': 'x'})
assert r.status_code == 400 and r.json()['code'] == 'MD_EDIT_NOT_FOUND'
print('PASS: 文档不存在拒绝')

# ── 7. 空内容 → MD_EDIT_EMPTY ──
r = client.put('/api/markdown/tr_edit001', json={'mode': 'notes', 'content': '   '})
assert r.status_code == 400 and r.json()['code'] == 'MD_EDIT_EMPTY'
print('PASS: 空内容拒绝')

# ── 8. 清除语义：重新生成（save_markdown 无 edited）→ 新 entry 无 edited 字段 ──
s.save_markdown(MarkdownView(id='md_new_notes', transcript_id='tr_edit001',
                             content='重新生成版', style=Style.MINIMAL, mode=GenerateMode.NOTES))
entry = s.get_markdowns_by_transcript('tr_edit001', mode='notes')[0]
assert 'edited' not in entry, "重新生成后 edited 应消失（标记自动清除）"
print('PASS: 重新生成清除 edited 标记')

# ── 9. transcript 存在但无产物 → MD_EDIT_NO_PRODUCT ──
s.save_transcript(RawTranscript(
    id='tr_edit002', title='No Product', source_type=SourceType.WEBPAGE,
    engine='trafilatura', raw_text='原文', content_hash='h2', metadata=RawMetadata(),
))
r = client.put('/api/markdown/tr_edit002', json={'mode': 'notes', 'content': 'x'})
assert r.status_code == 400 and r.json()['code'] == 'MD_EDIT_NO_PRODUCT'
print('PASS: 无产物拒绝')

# ── 10. 内容超限 → MD_EDIT_TOO_LARGE ──
orig_max = markdown_edit._MAX_CONTENT_CHARS
markdown_edit._MAX_CONTENT_CHARS = 10
try:
    r = client.put('/api/markdown/tr_edit001', json={'mode': 'notes', 'content': 'x' * 100})
    assert r.status_code == 400 and r.json()['code'] == 'MD_EDIT_TOO_LARGE'
finally:
    markdown_edit._MAX_CONTENT_CHARS = orig_max
print('PASS: 内容超限拒绝')

# ── 11. _unrewrite_asset_urls 边界 ──
fn = markdown_edit._unrewrite_asset_urls
assert fn('![a](/api/assets/tr_edit001/x.png)', 'tr_edit001') == '![a](assets/x.png)'
assert fn('![](/api/assets/tr_edit001/x.png)', 'tr_edit001') == '![](assets/x.png)'
# 其他文档 URL 不反转；<img> 形态（用户新增内容）不反转
assert fn('![a](/api/assets/other/y.png)', 'tr_edit001') == '![a](/api/assets/other/y.png)'
assert fn('<img src="/api/assets/tr_edit001/z.png">', 'tr_edit001') == '<img src="/api/assets/tr_edit001/z.png">'
print('PASS: _unrewrite_asset_urls 边界')

print('\n全部 PASS: test_markdown_edit')
