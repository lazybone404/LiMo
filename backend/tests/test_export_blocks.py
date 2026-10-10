"""语义标记 `limo-block` 的**导出与下游消费点** —— 四个出口 + 两个数据层。

协议与解析本身的测试在 `test_artifact_blocks.py`（读共享 fixture，与前端逐条对称）。
**本文件测的是「谁消费它、消费得对不对」**——那是协议解析全绿也照样会出问题的一半。

本文件要防的失败**全都是静默的**（断言全过、肉眼等于没有的那一类）：

  A. HTML 导出漏掉 `<div class="md-block">` 包装 → 标记行**原样出现在导出页上**给读者
     看（这正是用户最初提的现象：「解析失败剥掉的标记…在前端显示」）。断「块内文字
     还在」是绿不了它的 —— 内容一直都在。
  B. Word 侧漏加 `div.md-block` 分支 → 落进 `_render_block` 末尾的 `else`「未知块：
     递归子元素」→ 内容一个不少，只是没有强调块形态。与 A 同理，必须断到**结构层**
     （表格数 / 底纹 / 左边框）。
  C. 无标记的产物也注入了 `blocks.css` 或写了 `data-marker-decor` → 「导出与改造前
     逐字节等价」这条承诺**静默失效**：没有一条断言会红，而每一份历史产物的导出 HTML
     都多了一段 CSS。
  D. md / 纯文本导出没剥标记 → `.md` / `.txt` 文件开头/中间留着一行
     `<!-- limo-block:v1 … -->`（纯文本里它是**可见噪音**）。
  E. 合成管道没剥标记 → 标记行进模型 prompt、且被 `estimate_tokens` 算进逐篇配额。
  F. `chars` 把标记行也算进去 → 「字数」这个数从此与读者看到的字数不一致。
"""
from __future__ import annotations

import io
import shutil
import tempfile
import zipfile

from docx import Document
from docx.oxml.ns import qn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.core.artifact_blocks import strip_blocks
from app.core.dependencies import get_store
from app.core.errors import AppError
from app.core.transcript_meta import strip_meta
from app.core.types import (GenerateMode, MarkdownView, RawMetadata, RawTranscript,
                            SourceType, Style)
from app.generation.synthesis import _latest_notes_text
from app.routers import export as E
from app.routers.export import _build_selfcontained_html, _load_block_css
from app.services.docx_export import BAR_MARKER, FILL_MARKER, markdown_to_docx
from app.storage.store import Store

_OPEN = '<!-- limo-block:v1 type="key-takeaway" -->'
_CLOSE = '<!-- /limo-block -->'
BLOCK = f"{_OPEN}\n**核心结论**\n\n采用异步任务处理长文档，可以避免请求超时。\n{_CLOSE}"

MD = f"开场白。\n\n## 甲\n内容一\n\n{BLOCK}\n\n## 乙\n内容二\n"
PLAIN = "开场白。\n\n## 甲\n内容一\n\n## 乙\n内容二\n"


# ═══════════════════════════════════════════════════════════════
# 0. 夹具：一份**含标记**的 notes 产物（走真实 Store，非桩）
# ═══════════════════════════════════════════════════════════════

_TMP: list[str] = []


def _new_store() -> Store:
    d = tempfile.mkdtemp()
    _TMP.append(d)
    return Store(d)


_STORE = _new_store()
_STORE.save_transcript(RawTranscript(
    id='tr_blk001', title='标记样例', source_type=SourceType.WEBPAGE,
    engine='trafilatura', raw_text='# 原文\n\n正文', content_hash='blk1',
    metadata=RawMetadata(),
))
_STORE.save_markdown(MarkdownView(
    id='md_blk_notes', transcript_id='tr_blk001', content=MD,
    style=Style.DETAILED, mode=GenerateMode.NOTES,
))

app = FastAPI()
app.include_router(E.router, prefix="/api")
app.dependency_overrides[get_store] = lambda: _STORE


@app.exception_handler(AppError)
async def _app_error_handler(request, exc: AppError):
    # ⚠️ 与 main.py **同规则**（模式 #36）：显式 status_code 优先，未显式才 recoverable→200。
    # 与 `test_export_custom_zip.py` 逐字同源 —— 自造一份更宽松的契约会让判据失去意义。
    http_status = exc.status_code if exc._explicit_status else (
        200 if exc.recoverable else exc.status_code)
    return JSONResponse(status_code=http_status, content=exc.to_dict())


client = TestClient(app)


def _export(**kw) -> dict[str, bytes]:
    body = {'doc_ids': ['tr_blk001'], 'content_tabs': ['summary'], **kw}
    r = client.post('/api/export/custom', json=body)
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        return {n: zf.read(n) for n in zf.namelist() if not n.endswith('/')}


def _only(files: dict[str, bytes]) -> str:
    assert len(files) == 1, files.keys()
    return next(iter(files.values())).decode('utf-8')


def _html_tag(html: str) -> str:
    """`<html …>` 开标签本身 —— 装饰开关是写在这上面的属性。

    ⚠️ **不能拿整篇断子串**：内联的 `blocks.css` 里那些注释逐字写着
    `data-marker-decor="off"` / `limo-block`，于是 `'…' in html` 恒真、
    `'…' not in html` 恒假，两个方向都测不到东西。
    """
    return html[:html.index('>', html.index('<html')) + 1]


def _body(html: str) -> str:
    """`<body>` 之后的正文 —— 断言「标记行没露到读者眼前」必须排除 `<style>` 块：
    内联的 `blocks.css` **注释**里逐字写着示例标记（`<!-- limo-block:v1 type="…" -->`），
    拿整篇断 `'<!-- limo-block' not in html` 会恒假。
    """
    return html[html.index('<body>'):]


# ═══════════════════════════════════════════════════════════════
# A. HTML 链
# ═══════════════════════════════════════════════════════════════

def test_无标记_html_导出逐字节不变():
    """零回归：**没有标记的产物**导出 HTML 与「开关不存在」的改造前完全一样。

    对照 C：把注入闸门写成「无条件」时本条红 —— 而后果是每一份历史产物的导出 HTML
    都凭空多一段 CSS，没有任何断言会红、也没有任何用户会察觉（样式恰好没生效）。
    """
    base = _build_selfcontained_html(PLAIN, "标题")
    assert base == _build_selfcontained_html(PLAIN, "标题", marker_decor=False)
    assert base == _build_selfcontained_html(PLAIN, "标题", marker_decor=True)
    assert 'md-block' not in base
    assert 'data-marker-decor' not in base


def test_有标记_html_块被包成_div_而不是裸露的标记行():
    """对照 A：漏掉包装时标记行会**原样出现在页面上**给读者看。

    ⚠️ 同时断两件事：包装**在**（结构），标记行**不在**（症状）。只断前者的话
    「包了一层但仍把注释拼进正文」照样绿。
    """
    html = _build_selfcontained_html(MD, "标题")
    assert html.count('<div class="md-block" data-block-type="key-takeaway">') == 1
    # ⚠️ 判据是「**读者可见的正文里**没有标记行」而不是整篇子串 —— 内联的 blocks.css
    # 注释里必然含这个协议的名字与示例（那份文件通篇在讨论它）。
    assert '<!-- limo-block' not in _body(html)
    assert '<!-- /limo-block' not in _body(html)
    # 块内文字一字不少，块外正文也没丢
    for chunk in ("核心结论", "采用异步任务处理长文档", "开场白。", "内容一", "内容二"):
        assert chunk in html, chunk


def test_有标记_html_内联了_blocks_css():
    """⚠️ 断的是「导出页里出现了 `blocks.css` 的选择器」，不是「文件存在」——
    文件存在但没拼进 style 块，效果与没有一样（与卡片那条同款）。"""
    html = _build_selfcontained_html(MD, "标题")
    css = _load_block_css()
    assert '[data-marker-decor="off"]' in html
    assert '[data-marker-decor="off"]' in css      # 自证：这份 CSS 确实是标记规则那份
    assert '.md-block' in html


def test_blocks_css_引用的每个界面令牌在导出页里都有定义():
    """**成对常量的守卫**：`blocks.css` 里每处 `var(--tk-…)` 都必须在导出 HTML 里有定义。

    ⚠️ 导出主题（`HTML_THEMES`）是自包含的独立 CSS，**一个 `--tk-*` 都不提供** ——
    `var()` 无 fallback 时整条声明作废。漏一个的表现是「强调块少一块样式」，不报错、
    不抛异常，且**应用内预览里是好的**（那边令牌由主题系统提供）。这条是唯一能自动
    发现「blocks.css 新引了一个令牌而导出侧没补」的地方。
    """
    import re as _re
    css = _re.sub(r"/\*[\s\S]*?\*/", "", _load_block_css())   # 剥注释：注释里提到名字不算引用
    used = sorted(set(_re.findall(r"var\((--tk-[a-z-]+)", css)))
    assert used, "blocks.css 没引用任何 --tk-* —— 判据本身失效了（改名了？）"
    html = _build_selfcontained_html(MD, "标题")
    missing = [t for t in used if f"{t}:" not in html]
    assert missing == [], f"导出页缺少这些令牌的定义：{missing}"


def test_装饰开关_关时写_off_开时不写属性():
    """«关» 必须是**属性存在且为 off**（CSS 靠它归零装饰）；«开» 时属性**整个不存在**，
    而不是等于 `"on"` —— 后者把「零回归」从「选择器不匹配」退化成「取值相同」。
    """
    off = _build_selfcontained_html(MD, "标题", marker_decor=False)
    on = _build_selfcontained_html(MD, "标题", marker_decor=True)
    assert 'data-marker-decor="off"' in _html_tag(off)
    assert 'data-marker-decor' not in _html_tag(on)
    # ⚠️ 关掉装饰**不改变内容**（DOM 保留、只是 CSS 归零）—— 一字不少
    for chunk in ("核心结论", "采用异步任务处理长文档"):
        assert chunk in off, chunk


def test_card_骨架下块落在卡内():
    """骨架轴 ⊥ 标记轴：`card` 下强调块在**卡片内部**，而不是另起一节或被切卡逻辑吞掉。"""
    html = _build_selfcontained_html(MD, "标题", skeleton="card")
    assert html.count('<section class="md-card">') == 2      # 甲 / 乙
    i_card = html.index('<section class="md-card">')
    i_block = html.index('<div class="md-block"')
    assert i_block > i_card, "强调块跑到卡片外面去了"
    assert html.count('<div class="md-block"') == 1


def test_块内的_H2_不会被切成新卡():
    """标记块里的 `##` 不是文档结构 —— 切卡必须跳过块内（与前端 `markdown-scan` 同源）。"""
    md = f"开场\n\n## 甲\n内容\n\n<!-- limo-block:v1 type=\"key-takeaway\" -->\n## 这不是标题\n块内\n<!-- /limo-block -->\n"
    html = _build_selfcontained_html(md, "标题", skeleton="card")
    assert html.count('<section class="md-card">') == 1, "块内的 ## 被当成了新章节"


# ═══════════════════════════════════════════════════════════════
# B. Word 链
# ═══════════════════════════════════════════════════════════════

def _tables(data: bytes) -> list:
    return Document(io.BytesIO(data)).tables


def _paragraph_texts(data: bytes) -> list[str]:
    doc = Document(io.BytesIO(data))
    texts = [p.text for p in doc.paragraphs]
    for t in doc.tables:
        for row in t.rows:
            for cell in row.cells:
                texts.extend(p.text for p in cell.paragraphs)
    return texts


def test_word_无标记_零表格():
    """零回归：没有标记时 Word 结构与改造前一致（改造前也不产表格）。"""
    assert len(_tables(markdown_to_docx(PLAIN, "标题"))) == 0


def test_word_有标记_块包成单单元格表格():
    """对照 B：断在**结构层**（表格数 / 单行单列），不断「内容还在」——
    漏加 `div.md-block` 分支时内容由 `else` 兜底照旧渲染，那种断言绿得毫无意义。"""
    tables = _tables(markdown_to_docx(MD, "标题"))
    assert len(tables) == 1
    assert len(tables[0].rows) == 1
    assert len(tables[0].columns) == 1


def test_word_标记块的底纹与左边框真的生效():
    """结构层再深一层：只有表格没有底纹/竖线 = 一个白框（读者看不出这是重点）。"""
    tables = _tables(markdown_to_docx(MD, "标题"))
    tc = tables[0].rows[0].cells[0]._tc
    tcPr = tc.find(qn("w:tcPr"))
    assert tcPr is not None
    shd = tcPr.find(qn("w:shd"))
    assert shd is not None and shd.get(qn("w:fill")) == FILL_MARKER
    borders = tcPr.find(qn("w:tcBorders"))
    assert borders is not None, "缺少单元格左边框（强调块的关键视觉）"
    left = borders.find(qn("w:left"))
    assert left is not None and left.get(qn("w:color")) == BAR_MARKER


def test_word_标记行不出现在文档正文里():
    """**修的是现存泄漏缺陷**：BeautifulSoup 把 `<!-- … -->` 解析成 `Comment`
    （`NavigableString` 子类、**不是 Tag**）→ 走「非 Tag 即文本」的兜底分支 →
    标记被当成可见文字排成一个段落。这条断言就是那个缺陷的回归守卫。
    """
    texts = "\n".join(_paragraph_texts(markdown_to_docx(MD, "标题")))
    assert 'limo-block' not in texts
    assert '<!--' not in texts


def test_word_块内文字一字不少():
    """结构对了还要内容对 —— 不能把块内文字吞掉或复制一份。"""
    texts = "\n".join(_paragraph_texts(markdown_to_docx(MD, "标题")))
    assert texts.count("核心结论") == 1
    assert texts.count("采用异步任务处理长文档，可以避免请求超时。") == 1
    for chunk in ("开场白。", "内容一", "内容二"):
        assert chunk in texts, chunk


def test_word_card_骨架下标记块表格嵌在卡片表格内():
    """两个轴同时开：顶层 2 张卡，标记块那只表格**嵌在卡片里面**。

    ⚠️ `Document.tables` 只返回 **body 顶层**的表格 —— 嵌套表在 `cell.tables` 里。
    写成「顶层应该有 3 张」会红在这里，而实现其实是对的。
    """
    doc = Document(io.BytesIO(markdown_to_docx(MD, "标题", skeleton="card")))
    assert len(doc.tables) == 2, "卡片数不对（甲 / 乙）"
    nested = [inner for t in doc.tables
              for row in t.rows for cell in row.cells for inner in cell.tables]
    assert len(nested) == 1, "标记块没有嵌在卡片内部（或反过来，卡片嵌在块里）"
    assert 'limo-block' not in "\n".join(
        p.text for p in nested[0].rows[0].cells[0].paragraphs)


# ═══════════════════════════════════════════════════════════════
# C. 定制导出端点（md / plain / html / docx 四个格式各走一遍真链路）
# ═══════════════════════════════════════════════════════════════

def test_md_导出剥掉标记行且保留内容():
    """对照 D：`.md` 文件里留着 `<!-- limo-block:v1 … -->` —— 用户拍板「导出的文件应
    当是干净正文」。⚠️ 剥的是**标记行**，块内文字一个字不能少。"""
    out = _only(_export(formats=['md']))
    assert 'limo-block' not in out
    assert '核心结论' in out
    assert '采用异步任务处理长文档' in out
    assert '开场白。' in out


def test_plain_导出剥掉标记行且保留内容():
    """纯文本里 HTML 注释是**可见噪音**（读者会看到一整行 `<!-- … -->`），与 md 同理。"""
    out = _only(_export(formats=['plain']))
    assert 'limo-block' not in out
    assert '<!' not in out
    assert '核心结论' in out


def test_html_端点导出带强调块():
    out = _only(_export(formats=['html']))
    assert '<div class="md-block" data-block-type="key-takeaway">' in out
    assert '<!-- limo-block' not in _body(out)
    # ⚠️ paper（默认骨架）下 `--tk-*` 令牌也必须注入 —— 这条是「只有 skeleton=card 才注入」
    # 那个缺陷的回归守卫（表现：强调块在导出页上一点样式都没有，预览里却正常）。
    assert '--tk-accent:' in out


def test_html_端点跟随装饰开关():
    """预览与导出共用同一个开关（「用户在预览栏看到什么，导出就应该得到什么」）。"""
    off = _only(_export(formats=['html'], marker_decor=False))
    on = _only(_export(formats=['html'], marker_decor=True))
    assert 'data-marker-decor="off"' in _html_tag(off)
    assert 'data-marker-decor' not in _html_tag(on)


def test_docx_端点导出块是表格():
    """Word 走的是**转换**（不是剥离）：块在 .docx 里应当是「有边框底纹的块」。

    ⚠️ 走的是端点的默认参数（`marker_decor` 对它无意义 —— Word 没有装饰开关），
    与上面直接调 `markdown_to_docx` 的那组**成对**：一组测转换本身，一组测它真的
    被端点用上了（`_build_export_zip` 的 docx 分支传的是原始 `content` 而非 `processed`）。
    """
    blob = next(iter(_export(formats=['docx']).values()))
    doc = Document(io.BytesIO(blob))
    assert len(doc.tables) == 1
    assert 'limo-block' not in "\n".join(p.text for p in doc.paragraphs)


# ═══════════════════════════════════════════════════════════════
# D. 合成管道 —— 标记不进模型 prompt、不计 token 配额
# ═══════════════════════════════════════════════════════════════

def test_合成读原料时剥掉标记行():
    """对照 E：标记行进 prompt 会同时踩三条 —— 噪音、被 `estimate_tokens` 算进逐篇配额、
    材料里多一类我们专属的可控文本。⚠️ 块内文字**照旧进**（剥的只是标记行）。"""
    text = _latest_notes_text(_STORE, 'tr_blk001')
    assert 'limo-block' not in text
    assert 'limo-meta' not in text        # 自证头成对地也剥（现有行为，别被这次改动带坏）
    assert '核心结论' in text              # 块内文字照旧
    assert '采用异步任务处理长文档' in text


# ═══════════════════════════════════════════════════════════════
# E. 字数统计 —— 标记行不算「正文字数」
# ═══════════════════════════════════════════════════════════════

def test_chars_不把标记行算进去():
    """对照 F：`chars` 是「正文字数」，标记行是读者看不到的协议元素。

    ⚠️ 判据写成「差值**恰好**等于两行标记 + 各自的行尾换行」而不是「chars < 原文长度」——
    后者在「只剥了开标记、漏了闭标记」时照样绿。
    """
    entry = _STORE.get_markdowns_by_transcript('tr_blk001', mode='notes')[0]
    raw = strip_meta(entry['content'])
    assert 'limo-block' in raw, "夹具失效：产物里没有标记，这条测不到东西"
    assert entry['chars'] == len(strip_blocks(raw))
    assert len(raw) - entry['chars'] == len(_OPEN) + 1 + len(_CLOSE) + 1


def teardown_module(module):
    for d in _TMP:
        shutil.rmtree(d, ignore_errors=True)
