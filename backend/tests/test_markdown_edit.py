"""验证产物编辑路由 — PUT /api/markdown/{id}

产品模型（2026-10-10）：**编辑不产生版本** —— 编辑就地覆盖用户正看着的那一版
（`{mode}/v{N}.md`），版本号不变。此前的语义是「编辑即新建一版、把 AI 那版挤成
prev」，编辑两次 AI 那版就被物理覆盖（而用户以为自己在「保留历史版本」）。

守四条：① 就地覆盖（版本数不变）② `edited` 标记落**文件头**（删 index 重建后仍在）
③ URL 反转 + style 保留 ④ 版本选择与各类校验。"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.core.dependencies import get_store
from app.core.errors import AppError
from app.core.markdown_assets import rewrite_asset_urls, unrewrite_asset_urls
from app.core.transcript_meta import parse_artifact_meta
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

# ── 1. 编辑保存成功：就地覆盖（版本号不变）+ edited + style 保留 + 返回仍绝对 URL ──
r = client.put('/api/markdown/tr_edit001', json={
    'mode': 'notes',
    'content': '# 笔记（手动改）\n修正后的内容 ![图](/api/assets/tr_edit001/a.png)',
})
assert r.status_code == 200, f"PUT should 200, got {r.status_code}: {r.text}"
data = r.json()
assert data['edited'] is True
assert data['mode'] == 'notes'
assert data['version'] == 1, "编辑**就地覆盖**：版本号不变（编辑不产生版本）"
assert data['style'] == 'detailed', "编辑保存应保留原 style"
assert '修正后的内容' in data['content']
assert '/api/assets/tr_edit001/a.png' in data['content'], "返回 content 应仍是绝对 URL（前端渲染用）"
assert [v['version'] for v in data['versions']] == [1], "只有一版：编辑没有新建版本"
print('PASS: 编辑保存成功 + 就地覆盖 + edited + style 保留')

# ── 2. URL 反转：磁盘落盘为 assets/ 相对路径 ──
entry = s.get_markdowns_by_transcript('tr_edit001', mode='notes')[0]
assert entry['version'] == 1
assert 'assets/a.png' in entry['content'], "落盘应为 assets/ 相对路径"
assert '/api/assets/' not in entry['content'], "绝对 URL 不应写回磁盘"
print('PASS: 图片 URL 反转（绝对 → assets 相对）')

# ── 3. 就地覆盖的**代价**：被编辑那一版的原内容不再可读回 ──
#    这是产品决策而非缺陷（用户原话：「编辑只是用户在那个对应版本上做更改」，
#    系统不追踪编辑历史）。⚠️ 反过来说：**AI 生成的那一版也不该被编辑干掉**——
#    故下面 3b 断言「编辑 v1 时 v2 原封不动」，那才是「版本 = AI 产物」的真正含义。
mds = s.get_markdowns_by_transcript('tr_edit001', mode='notes')
assert len(mds) == 1, "编辑不该增加版本数"
assert '生成内容' not in mds[0]['content'], "编辑是覆盖，旧正文不保留（我们不为编辑留历史）"
assert '修正后的内容' in mds[0]['content']
print('PASS: 编辑就地覆盖（不产生版本、不留编辑历史）')

# ── 3b. 有多个版本时，编辑**只动被指定的那一版** ──
s.save_markdown(MarkdownView(id='md_v2', transcript_id='tr_edit001',
                             content='第二版 AI 产物', style=Style.DETAILED,
                             mode=GenerateMode.NOTES))
r = client.put('/api/markdown/tr_edit001', json={
    'mode': 'notes', 'content': '# 改的是 v1', 'version': 1,
})
assert r.status_code == 200, r.text
assert r.json()['version'] == 1, "应返回**被编辑的那一版**（用户在看的版本），不是最新版"
mds = s.get_markdowns_by_transcript('tr_edit001', mode='notes')
assert [m['version'] for m in mds] == [2, 1], "版本数与版本号都不变"
v2 = next(m for m in mds if m['version'] == 2)
v1 = next(m for m in mds if m['version'] == 1)
assert '第二版 AI 产物' in v2['content'], "编辑 v1 不得碰到 v2"
assert '改的是 v1' in v1['content'] and '修正后的内容' not in v1['content']
# 版本 2 的头里**没有** edit 键（它是生成的）→ 派生的 edited 为 False
assert v2['edited'] is False, "生成的那一版不该被标成已编辑"
print('PASS: 编辑指定版本 —— 只动那一版，其余版本原封不动')

# ── 3c. 编辑标记落在**文件头**，不在 index 条目上 ──
#    旧实现把它放在 index 条目上——删一次 index.json，编辑痕迹永久丢失。
#    （「删 index 重建后仍在」的端到端验证在 test_artifact_meta.py 第 7 节：那里
#    用独立的 store 实例做重建，避免污染本文件后续小节依赖的 index。）
_v1_head = parse_artifact_meta(v1['content'])[0]
assert (_v1_head.get('edit') or {}).get('edited') is True, \
    f"编辑标记必须进文件头，实得 {_v1_head.get('edit')}"
assert v1['edited'] is True, "读路径应从头派生出 edited"
print('PASS: 编辑标记落文件头（读路径从头派生 edited）')

# ── 3d. 不存在的版本 → MD_EDIT_NO_VERSION（**不回退到最新版**）──
#    写操作尤其不能静默回退：「我要改第 9 版」而实际改了最新版，用户不会发现。
r = client.put('/api/markdown/tr_edit001', json={
    'mode': 'notes', 'content': 'x', 'version': 9,
})
assert r.status_code == 404 and r.json()['code'] == 'MD_EDIT_NO_VERSION', r.text
print('PASS: 指定不存在的版本被拒绝（不回退最新版）')

# ── 4. polished 也可编辑（就地覆盖）──
s.save_markdown(MarkdownView(id='md_old_polished', transcript_id='tr_edit001',
                             content='整理版', style=Style.MINIMAL, mode=GenerateMode.POLISHED))
r = client.put('/api/markdown/tr_edit001', json={'mode': 'polished', 'content': '整理版（手动）'})
assert r.status_code == 200
p = r.json()
assert p['edited'] is True and p['version'] == 1 and p['mode'] == 'polished'
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

# ── 8. 清除语义：重新生成（save_markdown 不带 `edit` 键）→ 新版本不是「已编辑」──
#    ⚠️ 判据必须是**派生值 falsy**，不能沿用旧的 `'edited' not in entry`——现在
#    `edited` 由读路径从文件头**派生**（恒有值），「字段不存在」永远不成立（假绿）。
s.save_markdown(MarkdownView(id='md_new_notes', transcript_id='tr_edit001',
                             content='重新生成版', style=Style.MINIMAL, mode=GenerateMode.NOTES))
entry = s.get_markdowns_by_transcript('tr_edit001', mode='notes')[0]
assert entry['version'] == 3, "重新生成 = **新建一版**，不覆盖被编辑过的 v1"
assert not entry.get('edited'), "新生成的那一版不该带已编辑标记（头里没有 edit 键）"
print('PASS: 重新生成新建一版且无 edited 标记（编辑过的那版仍在）')

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

# ── 11. 资产 URL 反向改写边界（P2-18 起住 core/markdown_assets，与正向同模块）──
fn = unrewrite_asset_urls
assert fn('![a](/api/assets/tr_edit001/x.png)', 'tr_edit001') == '![a](assets/x.png)'
assert fn('![](/api/assets/tr_edit001/x.png)', 'tr_edit001') == '![](assets/x.png)'
# 其他文档 URL 不反转；<img> 形态（用户新增内容）不反转
assert fn('![a](/api/assets/other/y.png)', 'tr_edit001') == '![a](/api/assets/other/y.png)'
assert fn('<img src="/api/assets/tr_edit001/z.png">', 'tr_edit001') == '<img src="/api/assets/tr_edit001/z.png">'
# ★ 与正向**成对**（模式 #52）：GET 改写 → 编辑保存反转，必须逐字节回到原样。
#   两侧曾分居 store.py 与 markdown_edit.py，「改了正向忘了反向」不会有任何报错，
#   只表现为磁盘上悄悄多出一批绝对 URL（导出/二次渲染才开始裂图）。
_live = '![a](assets/img_0001.png)'
assert fn(rewrite_asset_urls(_live, 'tr_edit001'), 'tr_edit001') == _live, \
    '正/反不再是成对操作：往返没有回到原样'
print('PASS: unrewrite_asset_urls 边界 + 与正向往返成对')

# ── 12. 旧别名 style 在**读侧**同样归一（不是静默改写）──
# 2026-10-05 枚举值改形（`meeting_minutes` → `meeting`）之前的产物，索引里存的是旧别名。
# 编辑保存这条路径此前裸 `Style(...)` 构造 → 落进 except → **静默改成 minimal**
# （用户的「会议纪要」变「极简」，零报错）。`STYLE_ALIAS` 的兼容必须在**写侧
# （`_normalize_and_validate_style`）与读侧（本节）都生效** —— 只做一半与完全没做一样。
# ⚠️ `content_hash` 必须与上文任何一次导入都不同：Store 的内容去重会**静默拒绝**
# 重复 hash 的写入（返回 False，不抛错），随后 PUT 就会误报 MD_EDIT_NOT_FOUND。
s.save_transcript(RawTranscript(
    id='tr_legacy', title='Legacy', source_type=SourceType.WEBPAGE,
    engine='trafilatura', raw_text='原文', content_hash='h_legacy', metadata=RawMetadata(),
))
s.save_markdown(MarkdownView(
    id='md_legacy', transcript_id='tr_legacy', content='# 会议纪要\n内容',
    style='meeting_minutes', mode=GenerateMode.NOTES,  # ⚠️ 刻意传字符串：历史数据的真实形态
))
r_legacy = client.put('/api/markdown/tr_legacy', json={'mode': 'notes', 'content': '# 会议纪要\n改过'})
assert r_legacy.status_code == 200, r_legacy.text
assert r_legacy.json()['style'] == 'meeting', \
    f"旧别名必须在读侧也归一（否则静默退化成 minimal）：{r_legacy.json()['style']!r}"
print('PASS: 历史别名 style 编辑保存后归一为 meeting（不是静默变 minimal）')

print('\n全部 PASS: test_markdown_edit')
