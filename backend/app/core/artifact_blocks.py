"""产物正文语义标记 `limo-block` —— 解析 / 剥离 / 判定。

**与 `transcript_meta.py` 的分工**：那个管**第一行**的自证头（`<!-- limo-meta {json} -->`），
本模块管**正文中**的语义标记。两者都是「产物 Markdown 里的协议元素」，但降级策略不同 ——
自证头缺失有兜底语义（`无头 ≠ 损坏`），而标记缺失是**完全正常**：多数产物一条都没有，
没有标记的文章就是一篇普通文章。

**协议**（模型直接输出，见 `generation/prompt.py::get_markdown_block_format`）::

    <!-- limo-block:v1 type="key-takeaway" -->
    **核心结论**

    采用异步任务处理长文档，可以避免请求超时。
    <!-- /limo-block -->

**三条不变量**（照 `services/chart_renderer.py` 的图表围栏协议）:

1. **专用标记** —— 不属于任何通用语言，用户内容里出现概率≈0，零误伤；
2. **零残留** —— 未闭合 / 未知 type / 未知协议版本，一律**只剥标记行、内容原样保留**；
   降级结果与「从未写过标记」逐字相同（内容永不被吞）；
3. **围栏内零触碰** —— 代码块里的 `limo-block` 是**用户内容**，不解析、不剥离。

⚠️ **与 `strip_meta` 的关键区别**：`strip_meta` 只剥第一行（`transcript_meta.py:280`），
本模块**必须逐行扫** —— 标记在正文中间，只看头尾的实现对它完全无效。
"""

from __future__ import annotations

import html
import re

#: 协议版本。写在标记名里（`limo-block:v1`）。
#: ⚠️ 改语法时新增 `v2`，`v1` 解析器**照旧工作**；遇到不认识的版本按「未知开标记」剥掉
#: （零残留降级，内容不丢）。与前端 `markdown-markers.ts::PROTOCOL_VERSION` 同源。
PROTOCOL_VERSION = "v1"

#: **产物侧**（预览 / HTML / Word 导出）真写了渲染分支的块类型。
#:
#: ⚠️ 这六种在预览侧**全部是纯 CSS 形态**（`styles/blocks.css`），产物侧刻意不做 DOM
#: 结构分派 —— 那份 CSS 同时是导出侧读的单一真相源，而导出 HTML 由本模块的
#: `render_blocks_html` 生成。Python 这边**对所有 type 一视同仁**地包 `md-block`
#: （见 `render_blocks_html`），形态差异全留给 CSS。
#:
#: ⚠️ `steps` / `timeline`（2026-10-11 补）是**图表管道里「简单线性图」的替代**：
#: 横链 / 竖列这两种关系 Markdown 列表能保真表达，不值得为它付一整条渲染链
#: （`chart_layout` → PIL → PNG）。形状差异落在**块内子元素类型**上（`steps` 用
#: 无序列表 → `<ul>`；`timeline` 用有序列表 → `<ol>`），两者都是 Markdown 的自然
#: 产物，预览（React）与导出（`markdown_render.py`）两侧同构 —— 故仍是纯 CSS，
#: 不需要 Python 侧任何分支。
#:
#: ⚠️ `table` / `code` / `image`（2026-10-11 补）与上面六个**性质不同族**：前六个
#: 标记的是**普通段落**（「这一段文字是什么性质」），块内原本就是散文；这三个标记的
#: 是**富内容** —— 表格 / 代码块 / 图片本来就与正文不同质，加标记的全部作用就是外面
#: 多一层壳，块内一个字节都不动。⚠️ 导出侧对本族**也同样零分支**：`render_blocks_html`
#: 一视同仁地包 `md-block`，块内的 `<table>` / `<pre><code>` / `<img>` 由各自的渲染
#: 路径产出，与「有没有标记」无关。
ARTIFACT_BLOCK_TYPES = frozenset(
    {"key-takeaway", "alert", "recommend", "metric", "steps", "timeline",
     "table", "code", "image"}
)

#: **对话侧**（前端 `ai-chat/ChatMarkerContent.tsx`）真写了渲染分支的块类型。
#: ⚠️ 与 `ARTIFACT_BLOCK_TYPES` **刻意不相交** —— 产物 prompt 与对话 prompt 各教各的。
CHAT_BLOCK_TYPES = frozenset({"card-group", "follow-up", "info", "digest"})

#: **节级修饰符** `card` —— 唯一的「不是块」的 type（2026-10-11）。
#:
#: ⚠️ 它与上面两族的区别是**粒度**：那两族标记的是**块**（一段内容），`card` 标记的是
#: **节**（一个 `##` 标题及其全部后续内容，直到下一个同级标题）。语义是**「这一节值得
#: 单独成一张卡片」**，故标记写在标题行的**下面一行**（`markdown_sections.split_by_h2`
#: 据此把 `Section.card` 置真）。
#:
#: ⚠️ 为什么要有这个区分（不是洁癖）：模型很可能会把它**写在标题之前**（其余 type 都是
#: 包裹式，写成 `<!-- card -->\n## 标题` 是它的本能）。若当成块，`iter_headings` 会进入
#: 块内状态、把 `## 标题` 整个跳过 —— 这一节从文档结构里消失，切分器连「节」都看不到。
#: 当作**节修饰符**（`match_open` 返回 `None`）时，两种写法都退化成同一件事：
#: **该节不成卡，但结构与内容一字不少**。
#:
#: ⚠️ 标记行仍被 `MARKER_LINE_RE` 剥掉（零残留）；`parse_blocks` 里 `match_open` 之后
#: 紧跟的那道 `MARKER_LINE_RE` 剥除正好接住它，故**不需要**在 `parse_blocks` 里另开分支。
#:
#: ⚠️ 与前端 `markdown-scan.ts::SECTION_CARD_TYPE` 同源（fixture 的 `meta.section_types` 钉住）。
SECTION_CARD_TYPE = "card"

#: **协议已知**的全部块类型 = 上两者之并。
#:
#: ⚠️ **「协议知道这个 type」与「某一侧实现了渲染」是两件事**，故拆成三个集合。混为一谈的
#: 失效方式是静默的：把一个 type 加进这里就等于宣称「有人处理它」，而某个渲染方其实没有
#: 分支 → 该 type 被原样当正文吐出来，**零报错**。拆开后测试可以机器核对
#: 「并集相等 + 两两不相交」。
#:
#: ⚠️ **Python 侧只消费本集合**（`is_known_type`），另两个是**镜像声明** —— 产物导出
#: （`render_blocks_html`）对任何 type 都一视同仁地包 `md-block`，不认识的在 CSS 层退化成
#: 普通内容。保留它们是为了让「并集 == 两侧之和」这条断言在**两侧都成立**。
#:
#: ⚠️ 与前端 `markdown-scan.ts` 的三个同名集合**必须同源**，由
#: `frontend/src/test/fixtures/limo-blocks.json` 的 `meta` 三者钉住。
KNOWN_BLOCK_TYPES = ARTIFACT_BLOCK_TYPES | CHAT_BLOCK_TYPES

#: 开标记：`<!-- limo-block:vN type="xxx" -->`，独占一行。
#: ⚠️ 与前端 `markdown-markers.ts::OPEN_RE` **逐字同构**（同一份 fixture 钉住）。
#: ⚠️ `type` 值限定小写连字符 —— 属性值含 `>` 时本正则不匹配，自然退化为「未知行」被剥掉。
OPEN_RE = re.compile(
    r"^[ \t]*<!--[ \t]*limo-block:(?P<ver>v\d+)[ \t]+type=\"(?P<type>[a-z][a-z0-9-]*)\""
    r"[ \t]*-->[ \t]*$"
)

#: 闭标记：`<!-- /limo-block -->`，独占一行。
CLOSE_RE = re.compile(r"^[ \t]*<!--[ \t]*/limo-block[ \t]*-->[ \t]*$")

#: **该剥掉哪一行**的判据 —— 比 `OPEN_RE` / `CLOSE_RE` 都宽：任何**独占一行的、
#: 含 `limo-block` 的 HTML 注释**（含语法畸形、未知协议版本、孤儿闭标记）。
#:
#: ⚠️ **为什么必须放宽**：`OPEN_RE` 严格（`type` 值只允许 `[a-z][a-z0-9-]*`），于是
#: `<!-- limo-block:v1 type="a>b" -->` 这种模型笔误既不匹配开标记、也不匹配闭标记 ——
#: 若只按这两个正则剥，它会**原样留在正文里给读者看**。这正是「解析失败的标记不该出现在
#: 读者眼前」要挡的现象。`limo-block` 是我们专属的命名空间（专用标记的零误伤前提），
#: 用户自己写这个词的唯一动机就是在讨论本协议，剥掉一行注释不丢任何内容。
MARKER_LINE_RE = re.compile(r"^[ \t]*<!--[ \t]*/?limo-block.*-->[ \t]*$")

#: 代码围栏开关（``` 或 ~~~，允许前导空白）。
#: ⚠️ **本模块是这份判据的家** —— `markdown_sections.py` 从这里 import（两个行分类器
#: 都依赖它）。前端对应 `markdown-scan.ts::FENCE_TOGGLE_RE`。本扫描只做「切换」，不配对符号类型。
FENCE_RE = re.compile(r"^\s*(?:```|~~~)")


def _normalize(md: str) -> str:
    """CRLF → LF。

    ⚠️ 入口整体规范化（与 `markdown_sections.py::split_by_h2` 同款）：只在匹配时剥 `\\r`
    是不够的，分段内容里的**每一行**都可能带尾 `\\r`。行数不变，故行号语义不受影响。
    """
    return re.sub(r"\r\n?", "\n", md)


def has_blocks(md: str) -> bool:
    """廉价早短路 —— 只做子串判定，**不做围栏感知**。

    误判（用户正文里恰好写着 `limo-block` 这个词）只会多走一次慢路径，代价可忽略；
    反之漏判才是 bug，而子串判定不会漏。
    """
    return "limo-block" in md


def has_block(md: str, type_name: str) -> bool:
    """正文里是否存在**指定 type 的真块**。

    ⚠️ **不能用子串判**（`type_name in md`）：正文里讨论本协议时也会出现 `follow-up`
    这种词，那是**提到**不是**用了**。`chat.py` 的 `blocks` 统计正因如此走 `parse_blocks`，
    本函数与它同源。

    ⚠️ 判据是「有**这个 type 的块**」，**不是**「块里能抽出有用内容」—— 后者是各消费点
    自己的事（例如追问补调要的是 `followup.has_usable_followup`，它在本函数之上再问
    「块内抽得出问题吗」，因为**空壳块**对它是「不可用」而对本函数是「有」）。
    """
    if not has_blocks(md):
        return False
    return any(seg["kind"] == "block" and seg["type"] == type_name
               for seg in parse_blocks(md))


def is_known_type(block_type: str) -> bool:
    """该块类型是否在渲染白名单里。

    未知类型**不是错误**（可能是更新版本写的、或模型自创的）—— 渲染方据此决定
    「包一层强调块」还是「只渲染内容」，两条路都**不丢内容**。
    """
    return block_type in KNOWN_BLOCK_TYPES


def match_open(line: str) -> re.Match[str] | None:
    """匹配一行的**块**开标记；**协议版本不符、或该 type 是节修饰符时返回 `None`**。

    协议不符的行按「未知」剥掉（内容不吞）；节修饰符则**根本不开块** —— 见
    `SECTION_CARD_TYPE`（它标记的是「节」，不是块）。

    ⚠️ 这个判据被 `parse_blocks` 与 `markdown_sections.iter_headings` **共用** ——
    两侧各写一遍的症状是「预览里没这张卡、TOC 里却多一项」，且零报错（模式 #54）。

    ⚠️ 与前端 `markdown-scan.ts::matchBlockOpen` 同源。
    """
    m = OPEN_RE.match(line)
    if m is None or m.group("ver") != PROTOCOL_VERSION:
        return None
    return None if m.group("type") == SECTION_CARD_TYPE else m


def is_section_card(line: str) -> bool:
    """该行是不是「把这一节标成卡片」的**节修饰符**（`type="card"` 的开标记）。

    ⚠️ 与 `match_open` **刻意分开**：那个回答「这里是不是块的开头」（节修饰符不是），
    这个回答「这一节要不要成卡」（只有切分器问）。合成一个函数会让「card 不开块」与
    「card 标记了节」两条互不相干的判据绑在一起。

    ⚠️ 认**当前协议版本**：将来 `v2` 改语法时，`v1` 的 card 标记按未知行剥掉（零残留），
    与 `match_open` 的版本纪律一致。
    ⚠️ 与前端 `markdown-scan.ts::isSectionCard` 同源。
    """
    m = OPEN_RE.match(line)
    return (m is not None and m.group("ver") == PROTOCOL_VERSION
            and m.group("type") == SECTION_CARD_TYPE)


def closes_block(line: str) -> bool:
    """该行是否闭合标记块。"""
    return bool(CLOSE_RE.match(line))


def parse_blocks(md: str) -> list[dict[str, str]]:
    """把正文切成 `text` / `block` 交替的分段序列。

    每段形状::

        {"kind": "text",  "content": "..."}
        {"kind": "block", "type": "key-takeaway", "content": "..."}

    ⚠️ **无标记时返回单个 text 段，内容与输入逐字相同**（仅 CRLF 被规范化）——
    这是「零回归」的机器可验证形式，也是前端预览与后端导出**共用同一条降级路径**的依据。

    ⚠️ **`content` 是块内原文**（不 trim 内部、不剥 `**` 加粗）—— 它要原样交给
    Markdown 渲染器，任何预处理都会让「块内渲染」与「块外渲染」分叉。

    ⚠️ `type` 只做**语法**提取，白名单判定交给 `is_known_type()`：解析器不认识
    `warning` 不代表这条标记写坏了（可能来自更新版本的产物）。
    """
    src = _normalize(md)
    out: list[dict[str, str]] = []
    buf: list[str] = []
    block_lines: list[str] = []
    block_type: str | None = None
    in_fence = False

    def flush_text() -> None:
        if buf:
            out.append({"kind": "text", "content": "\n".join(buf)})
            buf.clear()

    for raw in src.split("\n"):
        # ⚠️ 围栏判定**排在标记判定之前**，且对块内同样生效 —— 块内代码块里的
        # `<!-- /limo-block -->` 是用户内容，不该提前闭合这个块。
        if FENCE_RE.match(raw):
            in_fence = not in_fence
            (block_lines if block_type is not None else buf).append(raw)
            continue
        if in_fence:
            (block_lines if block_type is not None else buf).append(raw)
            continue

        if block_type is None:
            m = match_open(raw)
            if m is not None:
                flush_text()
                block_type = m.group("type")
                block_lines = []
                continue
            # 未知协议版本 / 孤儿闭标记 / 语法畸形的标记行 → 剥掉该行（内容不吞）
            if MARKER_LINE_RE.match(raw):
                continue
            buf.append(raw)
        else:
            if closes_block(raw):
                out.append({"kind": "block", "type": block_type,
                            "content": "\n".join(block_lines)})
                block_type = None
                block_lines = []
                continue
            # 块内的**又一枚标记行**（嵌套 / 畸形）→ 剥掉该行，内容并入外层
            if MARKER_LINE_RE.match(raw):
                continue
            block_lines.append(raw)

    if block_type is not None:
        # ⚠️ **未闭合**（有开无闭）→ 降级为普通正文：开标记行已剥，内容一字不丢。
        flush_text()
        out.append({"kind": "text", "content": "\n".join(block_lines)})
    else:
        flush_text()
    return out


def strip_blocks(md: str) -> str:
    """剥掉全部标记行，**保留内容**。

    供「需要干净正文」的消费点使用：字数统计、合成管道的模型输入、md / 纯文本导出。

    ⚠️ **无标记时原样返回入参**（连 CRLF 都不动）—— 零回归；有标记时才走规范化，
    因为要逐行重建。
    ⚠️ **未闭合的开标记同样被剥掉** —— 与 `parse_blocks` 的降级口径一致，
    两侧不同源的症状是「预览里正常、导出里多一行注释」。
    """
    if not has_blocks(md):
        return md
    out: list[str] = []
    in_fence = False
    for raw in _normalize(md).split("\n"):
        if FENCE_RE.match(raw):
            in_fence = not in_fence
            out.append(raw)
            continue
        if in_fence:
            out.append(raw)
            continue
        if MARKER_LINE_RE.match(raw):
            continue
        out.append(raw)
    return "\n".join(out)


def render_blocks_html(md: str, render_fragment) -> str:
    """按标记分段渲染成 HTML，块段包成 `<div class="md-block" data-block-type="…">`。

    `render_fragment` 是**消费点自己的**「Markdown 片段 → HTML」实现 —— HTML 导出走
    KaTeX 保护链、Word 导出走无代码高亮链。本函数只管**分段与包裹**，不管怎么渲染；
    于是两个导出侧共用同一份消费循环，而不是各抄一遍（模式 #54：同一份判据两处实现，
    症状是「一处改了另一处没跟上」，且两份看起来都正常）。

    ⚠️ **无标记时逐字返回 `render_fragment(md)`**（一个字符都不动）—— 这是「导出与
    改造前逐字节等价」的落点，也是那条零回归断言能被写出来的前提。

    ⚠️ **空段必须丢掉**：它转成 HTML 是空串，但更糟的是在拼接结果里留下一个空行，
    让「各段拼起来 == 整篇渲染」不再成立。
    """
    if not has_blocks(md):
        return render_fragment(md)

    parts: list[str] = []
    for seg in parse_blocks(md):
        if not seg["content"].strip():
            continue
        inner = render_fragment(seg["content"])
        if seg["kind"] == "text":
            parts.append(inner)
        else:
            # ⚠️ `data-block-type` 写入前**必须转义**：类型值当前来自正则
            # `[a-z][a-z0-9-]*`（安全），但那条约束属于解析器、不属于这里。
            t = html.escape(seg["type"], quote=True)
            parts.append(f'<div class="md-block" data-block-type="{t}">{inner}</div>')
    return "\n".join(parts)
