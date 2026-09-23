"""
Markdown → HTML 公共渲染 — 导出链路（HTML / Word）共用同一个解析器

**为什么独立成模块**：原先这套逻辑长在 `routers/export.py` 里，Word 导出一旦也要用，
services 层就得反向 import router（分层倒置）。抽出来之后两条导出路径**共用同一份
markdown 理解**，「预览一致」原则才不会因为多了个导出格式而分叉。

`markdown_to_html` 的 `codehilite` 参数是关键分歧点：
- HTML 导出要 Pygments 逐 token 上色（`:True`，默认，行为与抽取前逐字节一致）
- Word 导出只要**代码块的纯文本 + 语言名**（`:False`）——docx 里每个 token 一个
  span 是纯噪音，还会把 `<pre>` 内容切得七零八落
"""

import logging
import re

logger = logging.getLogger(__name__)

# ── Pygments 语法高亮（可选依赖）──
try:
    from pygments import highlight  # noqa: F401  （HTML 导出侧使用）
    from pygments.formatters import HtmlFormatter
    from pygments.lexers import get_lexer_by_name, guess_lexer  # noqa: F401
    PYGMENTS_OK = True
except ImportError:
    PYGMENTS_OK = False


def preprocess_gfm(md: str) -> str:
    """预处理 GFM 特性（Python markdown 不原生支持）"""
    # 确保 GFM 表格前有空行 — Python markdown tables 扩展要求表格前有空行，
    # 但 GFM 标准允许表格紧跟前导文字。不加此行会导致"[文字]\n|表头|\n|---|"
    # 被当作段落文本输出（react-markdown + remark-gfm 可正常渲染）
    md = re.sub(r'(^[^\n|][^\n]*)\n(\|[^\n]+\|[ \t]*\n\|[-: |]+\|)',
               r'\1\n\n\2', md, flags=re.MULTILINE)
    # 删除线 ~~text~~ → <del>text</del>
    md = re.sub(r'~~(.+?)~~', r'<del>\1</del>', md)
    # 任务列表 - [ ] → disabled checkbox, - [x] → checked disabled checkbox
    md = re.sub(
        r'^- \[ \] (.+)$',
        r'- <input type="checkbox" disabled style="margin-right:6px;vertical-align:middle"> \1',
        md, flags=re.MULTILINE,
    )
    md = re.sub(
        r'^- \[x\] (.+)$',
        r'- <input type="checkbox" checked disabled style="margin-right:6px;vertical-align:middle"> \1',
        md, flags=re.MULTILINE,
    )
    # 时间戳 badge — Content-[mm:ss] → 蓝色, Screenshot-[mm:ss] → 橙色
    md = re.sub(
        r'Content-\[(\d{2}:\d{2})\]',
        r'<span class="ts-badge ts-content">▶ \1</span>', md)
    md = re.sub(
        r'Screenshot-\[(\d{2}:\d{2})\]',
        r'<span class="ts-badge ts-screenshot">📷 \1</span>', md)
    return md


def markdown_to_html(md: str, codehilite: bool = True) -> str:
    """统一的 Markdown → HTML 转换，含代码高亮（Pygments）+ 语言标签

    Args:
        md: 原始 Markdown（GFM 方言）
        codehilite: 是否启用 Pygments 逐 token 上色。Word 导出关掉它——
            语言名仍由 `add_lang_labels` 保留，代码文本原样取出即可。
    """
    md = preprocess_gfm(md)
    extensions = ["tables", "fenced_code", "sane_lists", "smarty", "md_in_html", "def_list", "abbr"]
    if codehilite and PYGMENTS_OK:
        extensions.append("codehilite")
    try:
        import markdown
        html = markdown.markdown(md, extensions=extensions)
    except ImportError:
        html = f"<pre>{md}</pre>"
    html = add_lang_labels(html)
    html = add_heading_ids(html)
    return html


def add_lang_labels(html: str) -> str:
    """在代码块前插入语言标签 <span class="lang-label">，匹配预览的代码语言栏"""
    def _insert_label(m: re.Match) -> str:
        lang = m.group(1)
        label = f'<span class="lang-label">{lang}</span>\n'
        return label + m.group(0)
    # 匹配 <pre><code class="language-xxx"> 或 codehilite 包裹的 <code class="language-xxx">
    return re.sub(
        r'(?:<div class="codehilite">\s*)?<pre><code class="language-(\w+)"[^>]*>',
        _insert_label,
        html,
    )


def add_heading_ids(html: str) -> str:
    """给 <h2>/<h3>/<h4> 追加 id 属性（算法对齐前端 headingId），
    使得 HTML 导出中的标题锚点链接可以定位"""
    def _make_id(text: str) -> str:
        # 先清除时间戳标记
        text = re.sub(r'(?:▶ |\*?Content-\[)?\d{2}:\d{2}\]?', '', text)
        text = re.sub(r'Screenshot-\[\d{2}:\d{2}\]', '', text)
        # 转小写、空白→短横、保留字母/数字/CJK
        text = text.lower().strip()
        text = re.sub(r'\s+', '-', text)
        text = re.sub(r'[^a-z0-9一-鿿\-]', '', text)
        text = re.sub(r'-+', '-', text)
        return text.strip('-')

    def _repl(m: re.Match) -> str:
        tag, inner = m.group(1), m.group(2)
        id_ = _make_id(inner)
        return f'<{tag} id="{id_}">{inner}</{tag}>'

    return re.sub(r'<(h[234])>(.+?)</\1>', _repl, html)
