"""
Word 导出（Markdown → .docx）测试

**为什么全部用例都把产物回读一遍**：docx 是 zip+XML 的二进制格式，生成时不报错
不等于内容正确 —— 段落样式名写错、字体槽位没设、图片关系没建立，全都能「成功」
产出一个 Word 打不开或内容缺失的文件。所以每个用例都用 `Document(BytesIO(...))`
回读，断言**读出来的结构**而不是「函数没抛异常」。

运行：python tests/test_docx_export.py
"""
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from docx import Document                       # noqa: E402
from docx.oxml.ns import qn                     # noqa: E402
from docx.shared import Inches, Pt              # noqa: E402

from app.services.docx_export import markdown_to_docx   # noqa: E402


def _reload(data: bytes) -> Document:
    """回读产物"""
    return Document(io.BytesIO(data))


def _all_text(doc: Document) -> str:
    parts = [p.text for p in doc.paragraphs]
    for t in doc.tables:
        for row in t.rows:
            for cell in row.cells:
                parts.append(cell.text)
    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════
# 1. 基础块级元素
# ═══════════════════════════════════════════════════════════

MD_BASIC = """# 文档大标题

这是第一段正文，含**粗体**、*斜体*、`行内代码` 和 ~~删除线~~。

## 二级标题

### 三级标题

> 这是一段引用文字。

---

- 无序项一
- 无序项二
  - 嵌套项 A
  - 嵌套项 B

1. 有序项一
2. 有序项二

[访问示例站点](https://example.com) 是链接。
"""

data = markdown_to_docx(MD_BASIC, "测试文档")
assert data[:2] == b'PK', "docx 必须是 zip 容器（PK 魔数）"

doc = _reload(data)
text = _all_text(doc)

for expect in ["文档大标题", "这是第一段正文", "二级标题", "三级标题",
               "这是一段引用文字", "无序项一", "嵌套项 A", "有序项一",
               "访问示例站点"]:
    assert expect in text, f"内容丢失：{expect!r}\n实际：{text[:400]}"
print("PASS: 标题/段落/引用/分隔线/列表(含嵌套)/链接 全部落到 docx")

# 标题走 Heading 样式（Word 导航窗格可见 = 大纲级别正确）
styles = [p.style.name for p in doc.paragraphs if p.text.strip()]
assert any(s.startswith("Heading") for s in styles), f"没有 Heading 样式段落：{styles}"
assert any(s == "List Bullet" for s in styles), f"无序列表样式缺失：{styles}"
assert any(s == "List Number" for s in styles), f"有序列表样式缺失：{styles}"
print("PASS: 标题用 Heading 样式（大纲级别正确）、列表用 List Bullet/Number")

# 行内格式
bold_found = italic_found = strike_found = False
for p in doc.paragraphs:
    for r in p.runs:
        if r.bold and "粗体" in r.text:
            bold_found = True
        if r.italic and "斜体" in r.text:
            italic_found = True
        if r.font.strike and "删除线" in r.text:
            strike_found = True
assert bold_found, "**粗体** 没有映射到 run.bold"
assert italic_found, "*斜体* 没有映射到 run.italic"
assert strike_found, "~~删除线~~ 没有映射到 run.font.strike"
print("PASS: 粗体/斜体/删除线 映射到对应的 run 属性")

# 中文字体槽位（漏设 w:eastAsia 会让中文掉回宋体）
eastasia_set = False
for p in doc.paragraphs:
    for r in p.runs:
        rFonts = r._element.find(qn('w:rPr'))
        if rFonts is not None:
            rf = rFonts.find(qn('w:rFonts'))
            if rf is not None and rf.get(qn('w:eastAsia')):
                eastasia_set = True
assert eastasia_set, "run 未设置 w:eastAsia —— 中文会掉回 Word 默认字体"
print("PASS: 中文走 w:eastAsia 槽位（与西文混排不掉字重）")


# ═══════════════════════════════════════════════════════════
# 2. 代码块
# ═══════════════════════════════════════════════════════════

MD_CODE = """# 代码

```python
def hello(name):
    return f"你好，{name}"

print(hello("世界"))
```

正文继续。
"""

doc = _reload(markdown_to_docx(MD_CODE, "代码测试"))
text = _all_text(doc)
assert "def hello(name):" in text, f"代码内容丢失：{text!r}"
assert 'return f"你好，{name}"' in text, "代码里的中文/引号丢失"
assert "print(hello(" in text, "代码末行丢失"
assert "python" in text, "代码语言名丢失"
# 代码文本不得被 HTML 转义残留
assert "&quot;" not in text and "&lt;" not in text, f"HTML 实体未还原：{text!r}"
# 代码行不得被拆成多个段落（应在一个段落内用换行符连接）
code_paras = [p for p in doc.paragraphs if "def hello" in p.text]
assert len(code_paras) == 1, f"代码块被拆成了 {len(code_paras)} 个段落"
assert "print(hello" in code_paras[0].text, "代码块未保持在同一段落内（缩进会散）"
mono = any(r.font.name == "Consolas" for r in code_paras[0].runs)
assert mono, "代码块未使用等宽字体"
print(f"PASS: 代码块完整保留（语言名 + 换行 + 等宽字体），未转义残留")


# ═══════════════════════════════════════════════════════════
# 3. 表格
# ═══════════════════════════════════════════════════════════

MD_TABLE = """# 表格

| 引擎 | 类型 | 许可 |
|------|------|------|
| pypdfium2 | 本地 | BSD-3 |
| markitdown | 本地 | MIT |
"""

doc = _reload(markdown_to_docx(MD_TABLE, "表格测试"))
assert len(doc.tables) == 1, f"表格数量不对：{len(doc.tables)}"
tb = doc.tables[0]
assert len(tb.rows) == 3, f"行数不对：{len(tb.rows)}"
assert len(tb.columns) == 3, f"列数不对：{len(tb.columns)}"
assert tb.cell(0, 0).text.strip() == "引擎", f"表头错位：{tb.cell(0,0).text!r}"
assert tb.cell(1, 0).text.strip() == "pypdfium2", f"数据错位：{tb.cell(1,0).text!r}"
assert tb.cell(2, 2).text.strip() == "MIT", f"末格错位：{tb.cell(2,2).text!r}"
assert tb.cell(0, 0).paragraphs[0].runs[0].bold, "表头未加粗"
print("PASS: 表格结构（行/列/表头加粗/单元格对齐）正确")

# GFM 表格紧跟前导文字（无空行）也必须被识别 —— _preprocess_gfm 的专项处理
MD_TABLE_TIGHT = """前导文字
| A | B |
|---|---|
| 1 | 2 |
"""
doc = _reload(markdown_to_docx(MD_TABLE_TIGHT, "紧凑表格"))
assert len(doc.tables) == 1, "表格前无空行时未被识别为表格（GFM 兼容失败）"
print("PASS: 表格紧跟前导文字（无空行）仍被识别为表格")


# ═══════════════════════════════════════════════════════════
# 4. 任务列表 / 时间戳 badge
# ═══════════════════════════════════════════════════════════

MD_GFM = """# GFM

- [x] 已完成的事
- [ ] 待办的事

Content-[01:23] 这里是内容时间戳。

Screenshot-[04:56] 这里是截图时间戳。
"""

doc = _reload(markdown_to_docx(MD_GFM, "GFM 测试"))
text = _all_text(doc)
assert "☑" in text, f"任务列表已完成项未渲染为勾选符号：{text!r}"
assert "☐" in text, f"任务列表未完成项未渲染为方框：{text!r}"
assert "已完成的事" in text and "待办的事" in text, "任务列表文字丢失"
assert "01:23" in text and "04:56" in text, "时间戳内容丢失"

# 时间戳必须带颜色（与预览栏的蓝/橙 badge 对应）
ts_colors = set()
for p in doc.paragraphs:
    for r in p.runs:
        if "01:23" in r.text or "04:56" in r.text:
            if r.font.color and r.font.color.rgb:
                ts_colors.add(str(r.font.color.rgb))
assert len(ts_colors) >= 2, f"时间戳 badge 未区分颜色：{ts_colors}"
print(f"PASS: 任务列表 → ☑/☐，时间戳 badge 保留双色 {sorted(ts_colors)}")


# ═══════════════════════════════════════════════════════════
# 5. 图片（含大图等比缩放）
# ═══════════════════════════════════════════════════════════

def _make_png(w: int, h: int) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 120, 80)).save(buf, format="PNG", dpi=(96, 96))
    return buf.getvalue()


img_bytes = _make_png(400, 200)
doc = _reload(markdown_to_docx(
    "# 图片\n\n![示意图](assets/demo.png)\n\n正文继续。\n",
    "图片测试", images={"demo.png": img_bytes},
))
assert len(doc.inline_shapes) == 1, f"图片未嵌入：{len(doc.inline_shapes)} 个 inline_shape"
shape = doc.inline_shapes[0]
assert abs(shape.width.inches - 400 / 96) < 0.05, \
    f"小图未按原尺寸插入：{shape.width.inches:.2f}in（期望 {400/96:.2f}）"
assert "正文继续" in _all_text(doc), "图片后的正文丢失"
print(f"PASS: 图片内嵌（{shape.width.inches:.2f}in × {shape.height.inches:.2f}in），后续正文保留")

# 超大图必须缩到版心内，否则 Word 里会撑破版面
big_bytes = _make_png(3000, 1500)
doc = _reload(markdown_to_docx(
    "![大图](assets/big.png)\n", "大图测试", images={"big.png": big_bytes}))
big_shape = doc.inline_shapes[0]
assert big_shape.width.inches <= 6.3, f"大图未缩放：{big_shape.width.inches:.2f}in"
assert abs(big_shape.width.inches / big_shape.height.inches - 2.0) < 0.02, \
    "大图缩放未保持宽高比"
print(f"PASS: 超大图等比缩放到版心内（{big_shape.width.inches:.2f}in，宽高比保持 2:1）")

# 图片缺失：不得丢内容，必须留下可读占位
doc = _reload(markdown_to_docx("![丢失的图](assets/nope.png)\n\n后续正文\n", "缺图"))
assert "后续正文" in _all_text(doc), "缺图时后续正文丢失"
assert "丢失的图" in _all_text(doc), "缺图时未留下任何占位提示"
print("PASS: 图片缺失 → 留占位文本且不吞掉后续正文")

# data URI 图片（_render_formulas_to_images 的产物形态）
import base64                                          # noqa: E402
data_uri = "data:image/png;base64," + base64.b64encode(img_bytes).decode()
doc = _reload(markdown_to_docx(f"![公式]({data_uri})\n", "data URI"))
assert len(doc.inline_shapes) == 1, "data URI 图片未解析"
print("PASS: data:image/png;base64 URI 可直接内嵌")


# ═══════════════════════════════════════════════════════════
# 6. 公式（保留 LaTeX 原文 —— 见模块设计说明）
# ═══════════════════════════════════════════════════════════

MD_FORMULA = """# 公式

行内公式 $E = mc^2$ 保留原文。

$$
\\int_0^1 x^2 dx = \\frac{1}{3}
$$
"""

doc = _reload(markdown_to_docx(MD_FORMULA, "公式测试"))
text = _all_text(doc)
assert "E = mc^2" in text, f"行内公式内容丢失：{text!r}"
assert "int_0^1" in text or "∫" in text, "块级公式内容丢失"
print("PASS: LaTeX 公式以原文保留（不静默丢弃）")


# ═══════════════════════════════════════════════════════════
# 7. 边界
# ═══════════════════════════════════════════════════════════

doc = _reload(markdown_to_docx("", "空文档"))
assert doc is not None, "空内容应产出可打开的空文档而非报错"
print("PASS: 空内容 → 合法空文档（不抛异常）")

doc = _reload(markdown_to_docx("没有任何 markdown 语法的纯文本一行。", "纯文本"))
assert "没有任何 markdown 语法的纯文本一行。" in _all_text(doc)
print("PASS: 无语法纯文本正常落为段落")

# 未闭合的语法（LLM 输出常见）：不得丢内容、不得抛异常
doc = _reload(markdown_to_docx("# 标题\n\n**未闭合的粗体\n\n`未闭合的行内代码\n", "残缺语法"))
text = _all_text(doc)
assert "未闭合的粗体" in text and "未闭合的行内代码" in text, f"残缺语法吞掉了内容：{text!r}"
print("PASS: 未闭合的 markdown 语法不吞内容（LLM 输出容错）")


# ═══════════════════════════════════════════════════════════
# 8. 文档元数据
# ═══════════════════════════════════════════════════════════

doc = _reload(markdown_to_docx("# 标题\n\n内容\n", "元数据标题"))
assert doc.core_properties.title == "元数据标题", \
    f"文档属性未写入标题：{doc.core_properties.title!r}"
print("PASS: docx 元数据 title 已写入（Word 属性面板可见）")


# ═══════════════════════════════════════════════════════════
# 9. 真实产物冒烟（用本仓库真实生成的笔记形态）
# ═══════════════════════════════════════════════════════════

MD_REAL = """# 视频笔记：Transformer 架构解析

> 来源：Bilibili | 时长 18:24

## 一、核心思想

Transformer 抛弃了循环结构，完全依赖**自注意力机制**（Self-Attention）。[01:24]

主要优势：

1. 并行计算能力强
2. 长距离依赖建模好
3. 可扩展性好

| 组件 | 作用 |
|------|------|
| Encoder | 编码输入序列 |
| Decoder | 生成输出序列 |

```python
# 一个注意力头的实现
attn = softmax(Q @ K.T / sqrt(d_k)) @ V
```

## 二、下面这张图说明了整体结构

![Transformer 架构图](assets/chart_1.png)

### 关键点

- [x] 理解自注意力
- [ ] 理解位置编码
"""

chart_png = _make_png(640, 400)
doc = _reload(markdown_to_docx(
    MD_REAL, "视频笔记：Transformer 架构解析",
    images={"chart_1.png": chart_png},
))
text = _all_text(doc)
assert len(doc.tables) == 1, "真实产物里的表格丢失"
assert len(doc.inline_shapes) == 1, "真实产物里的图表丢失"
assert "attn = softmax" in text, "真实产物里的代码块丢失"
assert "理解自注意力" in text and "理解位置编码" in text, "任务列表丢失"
h_count = sum(1 for p in doc.paragraphs if p.style.name.startswith("Heading"))
assert h_count >= 3, f"标题层级丢失：{h_count}"
print(f"PASS: 真实笔记形态端到端 —— {h_count} 个标题 / 1 表格 / 1 图片 / 代码块 / 任务列表")


# ═══════════════════════════════════════════════════════════
# 10. OOXML 元素顺序（Word 严格，python-docx 宽容）
#
# 手写 `w:shd`/`w:pBdr` 时若盲目 append，会排到 `w:spacing`/`w:ind` 之后 ——
# **python-docx 照常读得回来，只有 Word 会提示「文件已损坏」**。所以这个检查
# 必须自己写，且必须先证明它抓得到违规（否则和「断言存在但没生效」没区别）。
# ═══════════════════════════════════════════════════════════

from docx.oxml import OxmlElement               # noqa: E402
from docx.oxml.ns import qn as _qn              # noqa: E402
from app.services.docx_export import _PPR_ORDER, _RPR_ORDER    # noqa: E402


def _seq_violations(parent, order: list[str], label: str) -> list[str]:
    """检查 parent 下属于 order 序列的子元素是否按下标单调递增排列"""
    tags = [_qn(f'w:{n}') for n in order]
    last, out = -1, []
    for child in parent:
        if child.tag in tags:
            i = tags.index(child.tag)
            if i < last:
                out.append(f"{label}: {child.tag.split('}')[1]} 排在更靠后的元素之后")
            last = i
    return out


def _collect_violations(doc) -> list[str]:
    bad: list[str] = []
    paras = list(doc.paragraphs)
    for t in doc.tables:
        for row in t.rows:
            for cell in row.cells:
                paras.extend(cell.paragraphs)

    def _check_runs(p_el):
        for r in p_el.findall(_qn('w:r')):
            rPr = r.find(_qn('w:rPr'))
            if rPr is not None:
                bad.extend(_seq_violations(rPr, _RPR_ORDER, 'rPr'))
        # 超链接里的 run 不在 paragraph.runs 中，单独遍历
        for h in p_el.findall(_qn('w:hyperlink')):
            for r in h.findall(_qn('w:r')):
                rPr = r.find(_qn('w:rPr'))
                if rPr is not None:
                    bad.extend(_seq_violations(rPr, _RPR_ORDER, 'hyperlink>rPr'))

    for p in paras:
        pPr = p._p.find(_qn('w:pPr'))
        if pPr is not None:
            bad.extend(_seq_violations(pPr, _PPR_ORDER, 'pPr'))
        _check_runs(p._p)
    return bad


# ── 自证：故意构造乱序，检查必须报出来 ──
# 构造的正是**修复前 `_shade` 的真实写法**：先设 spacing（python-docx 内部插入），
# 再 append shd —— 于是 shd 落在 spacing 之后，违反序列顺序。
from docx import Document as _Doc              # noqa: E402
_probe = _Doc()
_p = _probe.add_paragraph()
_p.paragraph_format.space_after = Pt(6)        # 先有 w:spacing（下标 21）
_pPr = _p._p.get_or_add_pPr()
_pPr.append(OxmlElement('w:shd'))              # 再 append w:shd（下标 9）→ 违规
_probe_bad = _collect_violations(_probe)
assert _probe_bad, "顺序检查没能抓到故意构造的违规 —— 这个检查是死的，等于没写"
print(f"PASS: 顺序检查自证有效（复现修复前的写法，被抓到：{_probe_bad[0]}）")

# ── 真实产物必须零违规 ──
ALL_FEATURES_MD = """# 全要素文档

正文 **粗** *斜* `行内代码` ~~删~~ 与 [链接](https://example.com)。

> 引用块

```python
def f():
    return 1
```

| A | B |
|---|---|
| 1 | 2 |

---

- 列表项
"""
viol = _collect_violations(_reload(markdown_to_docx(ALL_FEATURES_MD, "顺序测试")))
assert not viol, "OOXML 元素顺序违规（Word 可能报文件已损坏）：\n  " + "\n  ".join(viol)
print("PASS: 全要素产物的 w:pPr / w:rPr 子元素顺序全部符合 ECMA-376")


# ═══════════════════════════════════════════════════════════
# 9. HTML 注释不进正文（产物自证头 / 模型写的说明性注释）
# ═══════════════════════════════════════════════════════════
# 背景：`.md` / `.html` 导出**刻意保留**产物自证头（让离开 `.limo` 的产物自己说明来历；
# HTML 里它本就是注释，浏览器不显示）。但 docx 是**纯渲染格式**，没有「源码」可留 ——
# 而 bs4 的 `Comment` 是 `NavigableString` 的**子类**，会命中下面遍历里的
# `isinstance(node, NavigableString)` 分支被当正文段落写出来，Word 里就表现为
# 孤零零一行 `<!-- limo-meta {…} -->`（模型写进正文的注释同理）。

COMMENT_MD = """<!-- limo-meta {"type":"artifact","identity":{"mode":"notes","version":1}} -->

# 标题

## 风险

<!-- 无闭合标记，本节按卡片展示 -->

这一节的正文。

```html
<!-- 代码块里的注释必须保留 -->
```
"""

_doc_c = _reload(markdown_to_docx(COMMENT_MD, "注释测试"))
_text_c = _all_text(_doc_c)

assert "limo-meta" not in _text_c, "产物自证头漏进了 Word 正文"
assert "无闭合标记" not in _text_c, "模型写的说明性注释漏进了 Word 正文"
assert "这一节的正文" in _text_c, "剥注释时误删了注释后面的正文"
assert "标题" in _text_c, "剥注释时误删了标题"
# ⚠️ 代码块内的注释是另一回事：那是用户**本来就要展示**的代码，必须留住。
assert "代码块里的注释必须保留" in _text_c, "代码块内的注释被误删（那是用户要展示的内容）"
print("PASS: HTML 注释不进 Word 正文，且代码块内的注释保留")

# ── 对照实验：证明上一条不是「本来就没有」──
# 直击根因：bs4 的 `Comment` 是 `NavigableString` 的**子类** —— 不剥的话，它必然被上面
# 遍历里的 `isinstance(node, NavigableString)` 分支接住、当正文写进 docx。
# 少了这条，「limo-meta not in text」可能因为别的原因（比如根本没解析出注释）而假绿。
from bs4 import BeautifulSoup as _BS, Comment as _Comment, NavigableString  # noqa: E402
assert isinstance(_BS("<!-- x -->", "html.parser").find(string=True), _Comment), \
    "对照实验失效：bs4 未把注释解析成 Comment，本用例的根因判断有误"
_raw_soup = _BS("<p>a</p><!-- ZZLEAK --><p>b</p>", "html.parser")
_leaked = [str(n).strip() for n in _raw_soup.children
           if isinstance(n, NavigableString) and str(n).strip()]
assert any("ZZLEAK" in s for s in _leaked), \
    f"对照实验失效：未剥注释时它没有落进 NavigableString 分支（实际 {_leaked!r}）"
print("PASS: 对照实验 —— 未剥时注释确实落进 NavigableString 分支（根因成立）")


print("\nAll docx export tests done")


# ═══════════════════════════════════════════════════════════
# pytest 适配层
# ═══════════════════════════════════════════════════════════
# ⚠️ 本文件是**脚本式**的：上面全部断言在**导入期**顺序执行，任一失败即
# `AssertionError` 中断导入 —— `python tests/test_docx_export.py` 与 `pytest`
# 两种跑法都因此变红。
#
# 那为什么还要有这个函数？因为 `scripts/check-all` 跑的是 `pytest tests/ -q`，
# 而**模块级裸断言不产生任何用例**：文件被 pytest 当成普通模块、收集数为 0，
# 于是它既不红也不计数 —— 「18 项全过」是个幻觉，实际一项都没跑
# （2026-10-11 代码审查查出，与 2026-10-06 在另外四个文件上踩的是同一个坑）。
#
# 哨兵的作用：若哪天有人把上面整段包进 `def main()` 却忘了调用，
# `_SUITE_COMPLETED` 就不会被定义 → 本函数 `NameError` → 红。
_SUITE_COMPLETED = True


def test_docx_export_suite():
    """占位入口 —— 断言在模块级（导入期已执行）。函数体刻意为空。"""
    assert _SUITE_COMPLETED

