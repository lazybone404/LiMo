"""
Markdown → Word (.docx) 导出

**为什么走 Markdown → HTML → docx 两跳**，而不是自己写 Markdown 解析器：
解析器（含 LiMo 特有的 GFM 方言：表格前空行、删除线、任务列表、时间戳 badge）
只应存在一份（`services/markdown_render.py`）。两跳换来的是**Word 导出与 HTML 导出
对同一份 Markdown 的理解永远一致** —— 这正是导出系统的「预览一致」原则。
多出来的那一跳（HTML → docx）只消费固定的一小撮标签，成本可控。

**依赖**：python-docx（已在依赖，Doc 引擎在用）+ beautifulsoup4（项目各处已用）。
零新增包、零体积增量。

**样式色值说明**：本项目前端禁止硬编码色值（六轴主题系统），但 .docx 是**离开应用
的静态产物**，不参与主题切换，Word 自身也只有一套固定色板。故此处用集中常量表，
不接 `--tk-*`（那些变量在 Word 里不存在）。
"""

import io
import logging
import re
from typing import Callable

from bs4 import BeautifulSoup, NavigableString, Tag
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from app.services.markdown_render import markdown_to_html
from app.core.artifact_blocks import render_blocks_html as _render_blocks_html
# H2 → 卡片切分：与前端 `splitByH2` / HTML 导出 `split_by_h2` **同一份判据**，
# 靠共享 fixture 钉住（`frontend/src/test/fixtures/h2-sections.json`）。
from app.services.markdown_sections import split_by_h2

logger = logging.getLogger(__name__)

# ── 样式常量 ──
FONT_LATIN = "Calibri"
FONT_EASTASIA = "微软雅黑"          # 中文正文；与 LiMo 应用内阅读观感一致
FONT_MONO = "Consolas"

COLOR_INK = RGBColor(0x2D, 0x31, 0x42)          # 正文（与图表 ink 同色系）
COLOR_MUTED = RGBColor(0x4F, 0x5D, 0x75)        # 次要/引用
COLOR_LINK = RGBColor(0x1A, 0x73, 0xE8)
COLOR_TS_CONTENT = RGBColor(0x1A, 0x73, 0xE8)   # 时间戳 badge：内容
COLOR_TS_SHOT = RGBColor(0xEB, 0x6C, 0x36)      # 时间戳 badge：截图

FILL_CODE = "F2F3F5"        # 行内代码 / 代码块底纹
FILL_QUOTE = "F7F8FA"       # 引用块底纹

BODY_SIZE = Pt(10.5)        # 中文五号
CODE_SIZE = Pt(9)

# 版心宽度：A4(210mm) − 左右各 1 英寸边距 ≈ 6.27in。图片超过它必须等比缩放，
# 否则 Word 会按原尺寸铺出去把版面撑破。
CONTENT_WIDTH_IN = 6.27

# 一级标题以下的 heading 级别映射（HTML h1 → Word Heading 1）
_MAX_HEADING = 6

# 时间戳 badge 的类名 → 颜色
_TS_BADGE_COLORS = {
    "ts-content": COLOR_TS_CONTENT,
    "ts-screenshot": COLOR_TS_SHOT,
}


# ═══════════════════════════════════════════════════════════════
# 底层 XML 工具（python-docx 未提供的高层 API）
# ═══════════════════════════════════════════════════════════════

# ECMA-376 规定的 `w:pPr` / `w:rPr` 子元素**顺序**。
# 这两个容器是 ordered sequence 而非集合：`w:shd` 必须排在 `w:spacing`/`w:ind` 之前，
# `w:pBdr` 又必须排在 `w:shd` 之前。python-docx 自己的 `get_or_add_*` 会保序，
# 但它没有暴露 `_insert_shd`/`_insert_pBdr`，手写元素时只能自己找位置。
# ⚠️ 乱序的失败模式很阴险：python-docx 照常能读回，只有 Word 会提示「文件已损坏」。
_PPR_ORDER = [
    "pStyle", "keepNext", "keepLines", "pageBreakBefore", "framePr", "widowControl",
    "numPr", "suppressLineNumbers", "pBdr", "shd", "tabs", "suppressAutoHyphens",
    "kinsoku", "wordWrap", "overflowPunct", "topLinePunct", "autoSpaceDE", "autoSpaceDN",
    "bidi", "adjustRightInd", "snapToGrid", "spacing", "ind", "contextualSpacing",
    "mirrorIndents", "suppressOverlap", "jc", "textDirection", "textAlignment",
    "textboxTightWrap", "outlineLvl", "divId", "cnfStyle", "rPr", "sectPr", "pPrChange",
]
_RPR_ORDER = [
    "rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike",
    "outline", "shadow", "emboss", "imprint", "noProof", "snapToGrid", "vanish",
    "webHidden", "color", "spacing", "w", "kern", "position", "sz", "szCs", "highlight",
    "u", "effect", "bdr", "shd", "fitText", "vertAlign", "rtl", "cs", "em", "lang",
    "eastAsianLayout", "specVanish", "oMath",
]


def _ordered_insert(parent, element, order: list[str]):
    """按 `order` 指定的顺序把 element 插进 parent（而非盲目 append）"""
    tags = [qn(f'w:{n}') for n in order]
    if element.tag not in tags:
        parent.append(element)
        return
    idx = tags.index(element.tag)
    for child in parent:
        if child.tag in tags and tags.index(child.tag) > idx:
            child.addprevious(element)
            return
    parent.append(element)


def _set_run_font(run, *, latin: str = FONT_LATIN, eastasia: str = FONT_EASTASIA,
                  size: Pt | None = None, color: RGBColor | None = None):
    """设置 run 字体。

    ⚠️ **必须同时设 `w:eastAsia`**：python-docx 的 `run.font.name` 只写 `w:ascii`/
    `w:hAnsi`，中文字符走的是 `w:eastAsia` 槽位——不设它，中文会掉回 Word 默认
    （宋体/等线），与西文混排时字重和行高都不一致。
    """
    run.font.name = latin
    if size is not None:
        run.font.size = size
    if color is not None:
        run.font.color.rgb = color
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.get_or_add_rFonts()
    rFonts.set(qn('w:ascii'), latin)
    rFonts.set(qn('w:hAnsi'), latin)
    rFonts.set(qn('w:eastAsia'), eastasia)


def _shade(paragraph, fill: str):
    """段落底纹（python-docx 无 API，只能手写 w:shd）"""
    pPr = paragraph._p.get_or_add_pPr()
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'), fill)
    _ordered_insert(pPr, shd, _PPR_ORDER)


def _set_left_bar(paragraph, color: str = "D0D4DC"):
    """段落左侧竖线（引用块用）"""
    pPr = paragraph._p.get_or_add_pPr()
    pBdr = OxmlElement('w:pBdr')
    left = OxmlElement('w:left')
    left.set(qn('w:val'), 'single')
    left.set(qn('w:sz'), '18')      # 1/8 pt 为单位 → 18 ≈ 2.25pt
    left.set(qn('w:space'), '8')
    left.set(qn('w:color'), color)
    pBdr.append(left)
    _ordered_insert(pPr, pBdr, _PPR_ORDER)


def _set_bottom_border(paragraph, color: str = "CCCCCC"):
    """段落底边框（`---` 分隔线用）"""
    pPr = paragraph._p.get_or_add_pPr()
    pBdr = OxmlElement('w:pBdr')
    bottom = OxmlElement('w:bottom')
    bottom.set(qn('w:val'), 'single')
    bottom.set(qn('w:sz'), '6')
    bottom.set(qn('w:space'), '1')
    bottom.set(qn('w:color'), color)
    pBdr.append(bottom)
    _ordered_insert(pPr, pBdr, _PPR_ORDER)


def _add_hyperlink(paragraph, url: str, text: str, *, size: Pt | None = None):
    """插入超链接。

    python-docx 至今没有超链接 API（issue #74 自 2014 年挂起），只能手写
    `w:hyperlink` + 建立外部关系。⚠️ 元素必须**按构建顺序** append 到 `w:p`，
    否则链接会跑到段尾、和后续 run 错位。
    """
    try:
        r_id = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
    except Exception as e:      # 关系建立失败（非法 URL 等）→ 退化为纯文本
        logger.warning("超链接关系建立失败，退化为纯文本: %s (%s)", url, e)
        run = paragraph.add_run(text)
        _set_run_font(run, size=size, color=COLOR_LINK)
        run.font.underline = True
        return

    hyperlink = OxmlElement('w:hyperlink')
    hyperlink.set(qn('r:id'), r_id)

    new_run = OxmlElement('w:r')
    rPr = OxmlElement('w:rPr')

    # ⚠️ 按 ECMA-376 的顺序插入（rFonts → color → sz → u），不能顺手 append
    rFonts = OxmlElement('w:rFonts')
    rFonts.set(qn('w:ascii'), FONT_LATIN)
    rFonts.set(qn('w:hAnsi'), FONT_LATIN)
    rFonts.set(qn('w:eastAsia'), FONT_EASTASIA)
    _ordered_insert(rPr, rFonts, _RPR_ORDER)

    color = OxmlElement('w:color')
    color.set(qn('w:val'), '1A73E8')
    _ordered_insert(rPr, color, _RPR_ORDER)

    if size is not None:
        sz = OxmlElement('w:sz')
        sz.set(qn('w:val'), str(int(size.pt * 2)))   # half-points
        _ordered_insert(rPr, sz, _RPR_ORDER)

    u = OxmlElement('w:u')
    u.set(qn('w:val'), 'single')
    _ordered_insert(rPr, u, _RPR_ORDER)

    new_run.append(rPr)
    t = OxmlElement('w:t')
    t.set(qn('xml:space'), 'preserve')
    t.text = text
    new_run.append(t)
    hyperlink.append(new_run)
    paragraph._p.append(hyperlink)


# ═══════════════════════════════════════════════════════════════
# 图片解析
# ═══════════════════════════════════════════════════════════════

def _decode_data_uri(src: str) -> bytes | None:
    """`data:image/png;base64,...` → bytes"""
    m = re.match(r'data:image/[a-zA-Z+]+;base64,(.+)$', src, re.DOTALL)
    if not m:
        return None
    import base64
    try:
        return base64.b64decode(m.group(1))
    except Exception:
        return None


def _fit_image_size(data: bytes) -> tuple[Inches, Inches] | None:
    """按原图宽高比算出**不超过版心宽**的显示尺寸。

    优先 PIL 读真实像素尺寸（结合 DPI 换算物理宽）；PIL 读不出（SVG 等）时返回
    None，交由调用方按原尺寸插入。
    """
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            px_w, px_h = im.size
            dpi = im.info.get("dpi", (96, 96))
            dpi_x = dpi[0] if isinstance(dpi, (tuple, list)) and dpi[0] else 96
    except Exception:
        return None
    if not px_w or not px_h:
        return None
    natural_in = px_w / float(dpi_x)
    if natural_in <= CONTENT_WIDTH_IN:
        width_in = natural_in
    else:
        width_in = CONTENT_WIDTH_IN
    # 极端窄图（如 1px 分隔线）给个下限，否则在 Word 里几乎不可见
    width_in = max(width_in, 0.3)
    return Inches(width_in), Inches(width_in * px_h / px_w)


def _insert_image(doc, data: bytes, alt: str = "") -> bool:
    """插入图片段落（居中）。返回是否成功。"""
    size = _fit_image_size(data)
    para = doc.add_paragraph()
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    try:
        if size:
            para.add_run().add_picture(io.BytesIO(data), width=size[0], height=size[1])
        else:
            para.add_run().add_picture(io.BytesIO(data), width=Inches(CONTENT_WIDTH_IN))
    except Exception as e:
        logger.warning("docx 插入图片失败: %s", e)
        run = para.add_run("[图片：" + alt + "]" if alt else "[图片]")
        _set_run_font(run, size=BODY_SIZE, color=COLOR_MUTED)
        return False
    return True


# ═══════════════════════════════════════════════════════════════
# 行内渲染
# ═══════════════════════════════════════════════════════════════

class _InlineStyle:
    """行内格式上下文 —— 嵌套标签（如 `<strong><em>`）需要继承累加"""

    __slots__ = ("bold", "italic", "strike", "code", "color")

    def __init__(self, bold=False, italic=False, strike=False, code=False, color=None):
        self.bold = bold
        self.italic = italic
        self.strike = strike
        self.code = code
        self.color = color

    def merged(self, **kw) -> "_InlineStyle":
        s = _InlineStyle(self.bold, self.italic, self.strike, self.code, self.color)
        for k, v in kw.items():
            setattr(s, k, v)
        return s


def _emit_text(paragraph, text: str, st: _InlineStyle):
    if not text:
        return
    run = paragraph.add_run(text)
    if st.code:
        _set_run_font(run, latin=FONT_MONO, eastasia=FONT_EASTASIA, size=CODE_SIZE)
    else:
        _set_run_font(run, size=BODY_SIZE)
    run.bold = st.bold
    run.italic = st.italic
    if st.strike:
        run.font.strike = True
    if st.color is not None:
        run.font.color.rgb = st.color
    if st.code:
        # 行内代码：底纹只能加在 run 上（w:shd 在 rPr 里同样合法，但须排在 w:u/w:fitText 之前）
        rPr = run._element.get_or_add_rPr()
        shd = OxmlElement('w:shd')
        shd.set(qn('w:val'), 'clear')
        shd.set(qn('w:color'), 'auto')
        shd.set(qn('w:fill'), FILL_CODE)
        _ordered_insert(rPr, shd, _RPR_ORDER)


def _inline(paragraph, node, st: _InlineStyle, images: dict[str, bytes],
            resolver: Callable[[str], bytes | None] | None):
    """递归渲染行内内容到段落"""
    for child in node.children:
        if isinstance(child, NavigableString):
            _emit_text(paragraph, str(child), st)
            continue
        if not isinstance(child, Tag):
            continue

        name = child.name.lower()

        if name in ("strong", "b"):
            _inline(paragraph, child, st.merged(bold=True), images, resolver)
        elif name in ("em", "i"):
            _inline(paragraph, child, st.merged(italic=True), images, resolver)
        elif name in ("del", "s", "strike"):
            _inline(paragraph, child, st.merged(strike=True), images, resolver)
        elif name == "code":
            _inline(paragraph, child, st.merged(code=True), images, resolver)
        elif name == "br":
            paragraph.add_run().add_break()
        elif name == "a":
            href = child.get("href", "")
            text = child.get_text()
            if href and not href.startswith("#"):
                _add_hyperlink(paragraph, href, text, size=BODY_SIZE)
            else:
                # 锚点链接（标题跳转）：Word 里无对应概念，退化为斜体文本
                _emit_text(paragraph, text, st.merged(italic=True))
        elif name == "img":
            _inline_image(paragraph, child, images, resolver, st)
        elif name == "input":
            # 任务列表 checkbox（_preprocess_gfm 产出）
            checked = child.has_attr("checked")
            _emit_text(paragraph, "☑ " if checked else "☐ ", st)
        elif name in ("span", "abbr", "sup", "sub", "u", "mark", "small", "big", "font", "label"):
            # 时间戳 badge 的类名形如 "ts-badge ts-content" —— 必须逐类名查表，
            # 整串 join 后查表永远不命中（类名是两个词）
            cls_list = child.get("class", []) or []
            color = next((_TS_BADGE_COLORS[c] for c in cls_list if c in _TS_BADGE_COLORS), None)
            if color is not None:
                _emit_text(paragraph, child.get_text(), st.merged(color=color, bold=True))
            else:
                _inline(paragraph, child, st, images, resolver)
        else:
            # 未知标签：递归取内容，不丢字（漏格式优于丢内容）
            _inline(paragraph, child, st, images, resolver)


def _inline_image(paragraph, img_tag, images: dict[str, bytes],
                  resolver, st: _InlineStyle):
    """行内图片（少见；正常图片是块级，此处兜底）"""
    data = _load_image_bytes(img_tag.get("src", ""), images, resolver)
    if not data:
        _emit_text(paragraph, "[图片]", st)
        return
    size = _fit_image_size(data)
    run = paragraph.add_run()
    try:
        if size:
            run.add_picture(io.BytesIO(data), width=size[0], height=size[1])
        else:
            run.add_picture(io.BytesIO(data))
    except Exception as e:
        logger.warning("docx 行内图片插入失败: %s", e)
        _emit_text(paragraph, "[图片]", st)


def _load_image_bytes(src: str, images: dict[str, bytes], resolver) -> bytes | None:
    """按 src 取图片字节：data URI → 直接解码；其余 → 查表（按文件名匹配）"""
    if not src:
        return None
    if src.startswith("data:"):
        return _decode_data_uri(src)
    base = src.split("/")[-1].split("?")[0]
    if base in images:
        return images[base]
    # 带 query/路径变体：宽松匹配一次
    for k, v in images.items():
        if k.split("/")[-1] == base:
            return v
    if resolver:
        try:
            return resolver(src)
        except Exception as e:
            logger.warning("图片 resolver 失败: %s (%s)", src, e)
    return None


# ═══════════════════════════════════════════════════════════════
# 块级渲染
# ═══════════════════════════════════════════════════════════════

def _render_code_block(doc, pre_tag, lang: str):
    """代码块：逐行段落 + 浅灰底纹 + 等宽字体。

    刻意**不用**「一格表格」方案：表格在 Word 里会被周围文本挤压变形，
    且跨页断行行为不可控。段落 + 底纹更稳，复制出去也是纯文本。
    """
    code_tag = pre_tag.find("code")
    text = (code_tag or pre_tag).get_text()
    text = text.rstrip("\n")

    if lang:
        head = doc.add_paragraph()
        head.paragraph_format.space_after = Pt(2)
        head.paragraph_format.space_before = Pt(6)
        run = head.add_run(lang)
        _set_run_font(run, size=Pt(8), color=COLOR_MUTED)
        run.bold = True

    para = doc.add_paragraph()
    para.paragraph_format.space_before = Pt(0 if lang else 6)
    para.paragraph_format.space_after = Pt(6)
    para.paragraph_format.left_indent = Pt(6)
    _shade(para, FILL_CODE)
    lines = text.split("\n")
    for i, line in enumerate(lines):
        run = para.add_run(line)
        _set_run_font(run, latin=FONT_MONO, eastasia=FONT_EASTASIA, size=CODE_SIZE)
        if i < len(lines) - 1:
            run.add_break()


def _render_list(doc, list_tag, level: int, images, resolver,
                 ordered: bool | None = None):
    """列表（递归处理嵌套）。level ≥ 0，最多缩进 3 层后不再加深但继续渲染。"""
    is_ordered = ordered if ordered is not None else (list_tag.name.lower() == "ol")
    style = "List Number" if is_ordered else "List Bullet"
    if level > 0:
        style = f"{style} {min(level + 1, 3)}"

    for li in list_tag.find_all("li", recursive=False):
        para = doc.add_paragraph(style=style)
        para.paragraph_format.space_after = Pt(2)
        if level >= 3:      # 内置样式只到 3 级，更深的用缩进补
            para.paragraph_format.left_indent = Pt(18 * (level + 1))

        # li 的直属行内内容（不含嵌套列表）
        # ⚠️ 必须用 `_SingleChild` 包住每个子节点：`_wrap` 对 Tag 会返回标签自身
        # （语义是「拿它当容器遍历其子节点」），而 void 元素（任务列表的 `<input>`）
        # 没有子节点 —— 直接 `_wrap` 会让 ☑/☐ 静默消失。
        for child in li.children:
            if isinstance(child, Tag) and child.name.lower() in ("ul", "ol"):
                continue
            _inline(para, _SingleChild(child), _InlineStyle(), images, resolver)

        # 嵌套列表
        for sub in li.find_all(["ul", "ol"], recursive=False):
            _render_list(doc, sub, level + 1, images, resolver)


class _SingleChild:
    """把单个节点包成「有 children」的容器，复用 `_inline` 的遍历逻辑"""
    __slots__ = ("children",)

    def __init__(self, node):
        self.children = [node]


def _wrap(node):
    """Tag 本身就有 `.children`，直接复用；裸字符串包一层"""
    return node if isinstance(node, Tag) else _SingleChild(node)


def _render_table(doc, table_tag, images, resolver):
    """表格：Table Grid 样式 + 首行加粗"""
    rows = table_tag.find_all("tr")
    if not rows:
        return
    n_cols = max(len(r.find_all(["td", "th"])) for r in rows)
    if n_cols == 0:
        return
    table = doc.add_table(rows=0, cols=n_cols)
    try:
        table.style = "Table Grid"
    except KeyError:
        logger.warning("docx 缺少 Table Grid 样式，表格将无边框")

    for r_idx, tr in enumerate(rows):
        cells = tr.find_all(["td", "th"])
        row = table.add_row()
        for c_idx, cell_tag in enumerate(cells):
            if c_idx >= n_cols:
                break
            cell = row.cells[c_idx]
            para = cell.paragraphs[0]
            is_header = cell_tag.name == "th" or r_idx == 0
            _inline(para, _wrap(cell_tag), _InlineStyle(bold=is_header), images, resolver)
            if is_header:
                for run in para.runs:
                    run.bold = True
    doc.add_paragraph()     # 表格后留空段，否则紧跟的段落会贴住表格


def _render_blockquote(doc, bq_tag, images, resolver):
    """引用块：左侧竖线 + 灰底 + 缩进"""
    inner_blocks = [c for c in bq_tag.children if isinstance(c, Tag)]
    if not inner_blocks:
        inner_blocks = [bq_tag]
    for blk in inner_blocks:
        para = doc.add_paragraph()
        para.paragraph_format.left_indent = Pt(12)
        para.paragraph_format.space_after = Pt(4)
        _set_left_bar(para)
        _shade(para, FILL_QUOTE)
        _inline(para, _wrap(blk), _InlineStyle(italic=False, color=COLOR_MUTED), images, resolver)


#: 卡片容器的底纹（Word 侧）。⚠️ 这是 docx 模块里**唯一**为卡片新增的色值 ——
#: 8 套皮肤在 Word 里会退化成 8 组硬编码色值，那是第二份色表（CSS 里那份才是真相源）。
#: 故 docx 侧的「卡片」= 「有边框和底纹的块」，**按内容形态判断，不按皮肤判断**。
FILL_CARD = "FAFAFB"


class _CellProxy:
    """让 `_render_block` 能往表格单元格里写内容。

    ⚠️ 只代理 `_render_block` 真正调用到的三个方法。python-docx 的 `_Cell` 自带
    `add_paragraph` / `add_table`，**唯独没有 `add_heading`** —— 而标题正是卡片里的
    第一块内容（卡片头就是那个 `##`），所以走代理是必须的，不是图省事。
    """

    def __init__(self, cell):
        self._cell = cell

    def add_paragraph(self, *a, **kw):
        return self._cell.add_paragraph(*a, **kw)

    def add_table(self, *a, **kw):
        return self._cell.add_table(*a, **kw)

    def add_heading(self, text: str = "", level: int = 1):
        # 与 python-docx `Document.add_heading` 同款：level 0 → Title，n → Heading n
        style = "Title" if level == 0 else f"Heading {level}"
        return self._cell.add_paragraph(text, style=style)


def _render_card(doc, section_tag, images, resolver, ctx: dict):
    """一张卡片 → 单单元格表格（边框 + 底纹）。

    ⚠️ **Word 没有圆角和投影**，所以「卡片」在这里只能是「有边框和底纹的块」。
    这是**能力边界**，不是实现省略 —— 验收按这个形态判断，不要拿它跟 HTML 侧比像素。
    """
    table = doc.add_table(rows=1, cols=1)
    try:
        table.style = "Table Grid"
    except KeyError:
        logger.warning("docx 缺少 Table Grid 样式，卡片将无边框")

    cell = table.rows[0].cells[0]
    # ⚠️ python-docx 新建单元格自带一个空段落，不删掉的话每张卡顶部多一个空行。
    first = cell.paragraphs[0]
    first._p.getparent().remove(first._p)
    _shade_cell(cell, FILL_CARD)

    inner = _CellProxy(cell)
    for child in section_tag.children:
        if isinstance(child, Tag):
            _render_block(inner, child, images, resolver, ctx)
        elif isinstance(child, NavigableString) and str(child).strip():
            para = cell.add_paragraph()
            _emit_text(para, str(child), _InlineStyle())
    # 空卡片（切出来但内容为空）保留表格本身 —— 与预览一致：卡片是容器，空也占位
    if not cell.paragraphs:
        cell.add_paragraph()


def _shade_cell(cell, fill: str):
    """表格单元格底纹。`_shade` 系列操作的是 `w:p` 的 `w:pPr`，而 cell 的底纹在
    `w:tcPr/w:shd` 上 —— 两者是不同的元素，不能复用。"""
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'), fill)
    tcPr.append(shd)


def _set_cell_left_bar(cell, color: str):
    """表格单元格左边框。

    ⚠️ 与 `_set_left_bar`（段落用 `w:pBdr`）**不是同一个元素** —— 单元格的边框在
    `w:tcPr/w:tcBorders` 上，复用不了（同 `_shade_cell` vs `_shade` 的关系）。
    """
    tcPr = cell._tc.get_or_add_tcPr()
    borders = OxmlElement('w:tcBorders')
    left = OxmlElement('w:left')
    left.set(qn('w:val'), 'single')
    left.set(qn('w:sz'), '18')       # 1/8 pt 单位 → 18 ≈ 2.25pt（与段落竖线同宽）
    left.set(qn('w:space'), '0')
    left.set(qn('w:color'), color)
    borders.append(left)
    tcPr.append(borders)


#: 语义标记块在 Word 侧的形态常量。⚠️ 与 `FILL_CARD` 同款的能力边界：Word 表达不了
#: CSS 变量，`blocks.css` 里的 `--tk-accent` 到这里只能落成一个固定色值 —— 这是
#: **能力边界不是实现省略**（8 套主题在 Word 里退化成 8 组色值才是错的）。
#: ⚠️ 刻意**不**复用 `FILL_CARD` + Table Grid —— 那样 `card` 骨架下「卡内标记块」
#: 会看起来像「一张卡里套着一张卡」。
FILL_MARKER = "FDF7F2"
BAR_MARKER = "EB6C36"


def _render_marker_block(doc, div_tag, images, resolver, ctx: dict):
    """一个语义标记块 → 单单元格表格（左侧竖线 + 淡底）。

    ⚠️ 走表格而不是段落底纹：块内容是**整段 Markdown**（可能多段、含列表/代码），
    段落级底纹只能逐段加、段与段之间会露出缝隙（与卡片同一个理由）。

    ⚠️ **本分支缺了也不会丢内容** —— `_render_block` 末尾的 `else` 会递归子元素。
    故测试必须断到「这一块被单单元格表格包裹 + 竖线/底纹生效」，断「内容还在」是绿不了的。
    """
    table = doc.add_table(rows=1, cols=1)
    cell = table.rows[0].cells[0]
    # ⚠️ 新建单元格自带一个空段落，不删掉每块顶部多一个空行（与卡片同款）
    first = cell.paragraphs[0]
    first._p.getparent().remove(first._p)
    _shade_cell(cell, FILL_MARKER)
    _set_cell_left_bar(cell, BAR_MARKER)

    inner = _CellProxy(cell)
    for child in div_tag.children:
        if isinstance(child, Tag):
            _render_block(inner, child, images, resolver, ctx)
        elif isinstance(child, NavigableString) and str(child).strip():
            para = cell.add_paragraph()
            _emit_text(para, str(child), _InlineStyle())
    if not cell.paragraphs:
        cell.add_paragraph()


def _render_block(doc, node, images: dict[str, bytes], resolver, ctx: dict):
    """渲染一个块级元素"""
    name = node.name.lower()

    if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
        level = min(int(name[1]), _MAX_HEADING)
        text = node.get_text().strip()
        if not text:
            return
        # 文档大标题（## 之前的一级标题）用 Title 样式，其余用 Heading N
        if level == 1 and not ctx.get("seen_body"):
            h = doc.add_heading(text, level=0)
            for run in h.runs:
                _set_run_font(run)
        else:
            ctx["seen_body"] = True
            h = doc.add_heading(text, level=level)
            for run in h.runs:
                _set_run_font(run)

    elif name == "p":
        # 段落里可能只有一张图片（markdown 图片独占段落）
        imgs = node.find_all("img")
        text = node.get_text().strip()
        if imgs and not text:
            for img in imgs:
                data = _load_image_bytes(img.get("src", ""), images, resolver)
                if data:
                    _insert_image(doc, data, img.get("alt", ""))
                else:
                    para = doc.add_paragraph()
                    run = para.add_run(f"[图片不可用：{img.get('alt', '')}]")
                    _set_run_font(run, size=BODY_SIZE, color=COLOR_MUTED)
            return
        para = doc.add_paragraph()
        para.paragraph_format.space_after = Pt(6)
        _inline(para, node, _InlineStyle(), images, resolver)

    elif name == "pre":
        _render_code_block(doc, node, ctx.pop("pending_lang", "") or _guess_lang(node))

    elif name == "div" and "codehilite" in (node.get("class") or []):
        pre = node.find("pre")
        if pre is not None:
            _render_code_block(doc, pre, _guess_lang(pre))

    elif name in ("ul", "ol"):
        _render_list(doc, node, 0, images, resolver)

    elif name == "table":
        _render_table(doc, node, images, resolver)

    elif name == "blockquote":
        _render_blockquote(doc, node, images, resolver)

    # ⚠️ 卡片分支必须加在 elif 链**中间**：下方 `else` 是「未知块 → 递归子元素，
    # 尽量不丢内容」，排在它后面的分支**永远不可达**。也正因为有那个兜底，
    # 漏加本分支**不会丢内容**（section 被当未知块递归、正文照旧渲染）——
    # 所以测试必须断到「该节被单单元格表格包裹」这一层，断「内容还在」是绿不了的。
    elif name == "section" and "md-card" in (node.get("class") or []):
        _render_card(doc, node, images, resolver, ctx)

    elif name == "div" and "md-block" in (node.get("class") or []):
        _render_marker_block(doc, node, images, resolver, ctx)

    elif name == "div" and "md-cards" in (node.get("class") or []):
        # 卡片容器：本身不出块，逐个渲染里面的 section（各自成卡）
        for child in node.children:
            if isinstance(child, Tag):
                _render_block(doc, child, images, resolver, ctx)

    elif name == "hr":
        para = doc.add_paragraph()
        para.paragraph_format.space_before = Pt(4)
        para.paragraph_format.space_after = Pt(4)
        _set_bottom_border(para)

    elif name == "span" and "lang-label" in (node.get("class") or []):
        # `add_lang_labels` 把语言名插在代码块前，记下来给下一个 pre 用
        ctx["pending_lang"] = node.get_text().strip()

    elif name in ("dl",):
        for dt in node.find_all("dt"):
            para = doc.add_paragraph()
            run = para.add_run(dt.get_text())
            _set_run_font(run, size=BODY_SIZE)
            run.bold = True
        for dd in node.find_all("dd"):
            para = doc.add_paragraph()
            para.paragraph_format.left_indent = Pt(18)
            _inline(para, _wrap(dd), _InlineStyle(), images, resolver)

    elif name in ("img",):
        data = _load_image_bytes(node.get("src", ""), images, resolver)
        if data:
            _insert_image(doc, data, node.get("alt", ""))

    elif name in ("script", "style", "head"):
        return

    else:
        # 未知块：递归其子元素，尽量不丢内容
        for child in node.children:
            if isinstance(child, Tag):
                _render_block(doc, child, images, resolver, ctx)
            elif isinstance(child, NavigableString) and str(child).strip():
                para = doc.add_paragraph()
                _emit_text(para, str(child), _InlineStyle())


def _guess_lang(pre_tag) -> str:
    """从 `<code class="language-xxx">` 取语言名"""
    code = pre_tag.find("code")
    if code is None:
        return ""
    for cls in code.get("class", []) or []:
        if cls.startswith("language-"):
            return cls[len("language-"):]
    return ""


# ═══════════════════════════════════════════════════════════════
# 公开入口
# ═══════════════════════════════════════════════════════════════

def markdown_to_docx(
    md: str,
    title: str = "",
    *,
    images: dict[str, bytes] | None = None,
    resolver: Callable[[str], bytes | None] | None = None,
    skeleton: str = "paper",
) -> bytes:
    """Markdown → .docx 字节流

    Args:
        md: Markdown 内容
        title: 文档标题（写入正文首行 Title 样式 + docx 元数据）
        images: 已收集的图片 {文件名: 字节}（图片内嵌的首选来源）
        resolver: 兜底图片解析回调 `src -> bytes | None`（图片不在 images 里时调用）
        skeleton: `"card"` 时按 H2 切成单单元格表格；默认 `"paper"` = 改造前形态。

    ⚠️ **刻意不接收 `card_style`**：Word 表达不了圆角/投影/渐变，8 套皮肤在这里会
    退化成 8 组硬编码色值 —— 那就是 `cards.css` 之外的第二份色表，正是「同一份判据
    不得有两份实现」要防的。Word 侧的卡片 = 「有边框和底纹的块」，只有一种。

    Returns:
        .docx 文件的完整字节（可直接作为 Response body / zip 条目）
    """
    images = images or {}
    doc = Document()

    # 元数据（Word 文件属性面板）
    if title:
        try:
            doc.core_properties.title = title
        except Exception:
            pass

    def _to_html(fragment: str) -> str:
        # Word 侧专用：`codehilite=False`（docx 里逐 token 的 `<span>` 是噪音）
        return markdown_to_html(fragment, codehilite=False)

    if skeleton == "card":
        # ⚠️ 逐段各自转 HTML 后拼串，**不**用一篇 HTML 再切 —— 切分器的判据是
        # Markdown（与前端同源），一旦先转 HTML，两侧就在比不同的东西了。
        # ⚠️ 顺序也是 Markdown 层的：**先切卡、再在每节内解析标记**（与前端同款）。
        # 反过来的话块会跨节，而跨节标记属于异常，会在降级里自然消解。
        preamble, sections = split_by_h2(md)
        chunks: list[str] = []
        if preamble.strip():
            chunks.append(_render_blocks_html(preamble, _to_html))
        for heading, section_body in sections:
            inner = _render_blocks_html(f"{heading}\n{section_body}", _to_html)
            chunks.append(f'<section class="md-card">{inner}</section>')
        html = "\n".join(chunks)
    else:
        html = _render_blocks_html(md, _to_html)
    soup = BeautifulSoup(html, "html.parser")
    body = soup.body or soup

    ctx: dict = {"pending_lang": "", "seen_body": False}

    for node in body.children:
        if isinstance(node, Tag):
            _render_block(doc, node, images, resolver, ctx)
        elif isinstance(node, NavigableString) and str(node).strip():
            para = doc.add_paragraph()
            _emit_text(para, str(node), _InlineStyle())

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
