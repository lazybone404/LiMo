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
#: ⚠️ 这些类型在预览侧**全部是纯 CSS 形态**（`styles/blocks.css`），产物侧刻意不做 DOM
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
#: ⚠️ `cycle`（2026-10-20 补）是同一判据的第三个成员，但方向不同：它替代的**不是**
#: 某张图，而是「模型想表达循环关系时的唯一可用出口」。图表管道从来就没有环形算子
#: （`_LAYOUTS` 十个：hchain/layers/timeline/grid/branch/tree/flowchart/state/
#: sequence/quadrant），而 `flowchart` 遇环直接抛错、`state` 只适合带状态的迁移图
#: —— 于是模型表达「A→B→C→A」时只能硬套 `state`，层级一深就撞 `nrow > 7` 的硬上限
#: （实测失败产物里 2 条正是这个）。`cycle` 把这条路变成**填空**：模型只写「有哪几个
#: 环节、顺序如何」（有序列表），环形布局由 `blocks.css` 的 `:nth-child` 角度档算，
#: 节点坐标、圆环半径、连接全部不经模型之手 —— 与图表管道的「只教语义、坐标由后端算」
#: 是同一条纪律，只是这次连后端都不用，纯 CSS 就够。
#: ⚠️ 块内仍是**有序列表**（`<ol>`），与 `timeline` 同形态、同归零出口 —— 但两者
#: **语义不同、不能互替**：`timeline` 是一条有始有终的线，`cycle` 是首尾相接的环。
#: 模型写错类型时降级形态相同（普通列表），区别只在「读起来是否成立」。
#:
#: ⚠️ `sequence` / `quadrant`（2026-10-20 补）是「用卡片代替画图」这条判据的第四、五个
#: 成员 —— 与 `steps` / `timeline` / `cycle` 同一批思路：图表管道里那两种布局（多参与者
#: 消息往来、二维四象限）的关系，Markdown 结构本身就能保真表达，不值得付一整条渲染链。
#:   · `sequence` 的骨架也是**有序列表**（按发生顺序），每项以 `**发送方 → 接收方**：`
#:     开头 —— 方向由文字承担，故它仍是纯 CSS（与 `timeline` 同出口：块内是 `<ol>`）。
#:     它**不是**生命线时序图：参与者列位需要结构分派，而那会打破「形态差异全在 CSS」
#:     这条纪律（`blocks.css` 同时是导出侧读的单一真相源）。
#:   · `quadrant` 的骨架仍是**有序列表**，恰好 4 项、顺序固定为左上 / 右上 / 左下 / 右下
#:     （与 `chart_layout` 的 `quadrant` 算子同序）。CSS 把它排成 2×2 网格。
#: ⚠️ 两者的**算子都还在** `chart_layout` 的 `_LAYOUTS` 里 —— 删的是 prompt 引导，
#: 不是能力（同 `hchain` / `timeline` 的先例，历史产物与手工调用不受影响）。
#:
#: ⚠️ `table` / `code` / `image`（2026-10-11 补）与上面九个**性质不同族**：那九个
#: 标记的是**普通段落**（「这一段文字是什么性质」），块内原本就是散文 / 列表；这三个
#: 标记的是**富内容** —— 表格 / 代码块 / 图片本来就与正文不同质，加标记的全部作用就是
#: 外面多一层壳，块内一个字节都不动。⚠️ 导出侧对本族**也同样零分支**：`render_blocks_html`
#: 一视同仁地包 `md-block`，块内的 `<table>` / `<pre><code>` / `<img>` 由各自的渲染
#: 路径产出，与「有没有标记」无关。
ARTIFACT_BLOCK_TYPES = frozenset(
    {"key-takeaway", "alert", "recommend", "metric", "steps", "timeline", "cycle",
     "sequence", "quadrant", "table", "code", "image"}
)

#: **「用列表代替画图」那一族** —— 勾了图表却产出这几种块，是照做了引导，不是漏了。
#:
#: 由来：这些关系过去要付一整条渲染链（语义 JSON → `chart_layout` → `chart_svg` →
#: PIL），而 Markdown 列表本身就能保真表达。2026-10-11 起 `steps` / `timeline`、
#: 2026-10-20 起 `cycle` / `sequence` / `quadrant` 陆续从图表引导迁移到语义块。
#:
#: ⚠️ **是 tuple 不是 frozenset**：顺序要稳定 —— `get_markdown_block_format()` 的
#: 「用列表表达就够了」那句文案由它拼出来，集合的迭代序在不同进程间不保证一致。
#: `in` 判定对 5 个元素完全够用，不需要哈希。
#:
#: ⚠️ **单一真相源**：判据在 `routers/generate.py::_chart_missing_warning`（完成期
#: warning）、文案在 `generation/prompt.py`（教模型用哪些块）。三处手抄的失效形态是
#: 「prompt 教了第五种、warning 只认前四种」→ 用户照做反而被报「图表未生成」。
CHART_ALTERNATIVE_BLOCK_TYPES: tuple[str, ...] = (
    "steps", "timeline", "cycle", "sequence", "quadrant",
)

assert set(CHART_ALTERNATIVE_BLOCK_TYPES) <= ARTIFACT_BLOCK_TYPES, (
    "「用列表代替画图」族必须是产物侧块类型的子集 —— 否则 prompt 在教一个"
    "渲染层不认识的 type")

#: **对话侧**（前端 `ai-chat/ChatMarkerContent.tsx`）真写了渲染分支的块类型。
#: ⚠️ 与 `ARTIFACT_BLOCK_TYPES` **刻意不相交** —— 产物 prompt 与对话 prompt 各教各的。
#: ⚠️ `caution` / `how-to` / `quote`（2026-10-12 补）与 `info` / `digest` 同族：
#: 一律不解析子结构，形态差异全落在 CSS 外壳上。
CHAT_BLOCK_TYPES = frozenset(
    {"card-group", "follow-up", "info", "digest", "caution", "how-to", "quote"}
)

#: **合成侧**（多篇合成产物）专用的块类型 —— 第四类（2026-10-18）。
#:
#: 目前只有一个成员 `evidence`：**证据块**。正文里紧跟它所支撑的论断，块内写
#: 方法 / 样本 / 范围 / 局限 / 反例（不是逐字原文 —— 那是引用，留在正文）。
#: 预览渲染成 `<details>` 折叠卡，导出由 `linearize_evidence()` 统一线性化。
#:
#: ⚠️ **刻意不进 `ARTIFACT_BLOCK_TYPES`**，两个理由缺一不可：
#: ① 那个集合被 `test_block_prompt.py` 遍历断言「每个 type 都出现在
#:    `get_markdown_block_format()` 里」，加进去就得把证据规范写进**单篇笔记**的提示词
#:    —— 而证据块只属于多篇合成（单篇材料没有「跨文档分歧」可写）；
#: ② `ARTIFACT_BLOCK_TYPES` 那批是**产物侧通用**的（任何产物都可能出现），证据块是**合成形态契约**的一部分，
#:    归属不同的产品边界。拆开后「协议知道」与「某侧实现了」仍然分得开。
SYNTHESIS_BLOCK_TYPES = frozenset({"evidence"})

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

#: **协议已知**的全部块类型 = 四个集合之并（产物 / 对话 / 合成 + 节修饰符单列）。
#:
#: ⚠️ **「协议知道这个 type」与「某一侧实现了渲染」是两件事**，故拆成三个块集合。混为一谈的
#: 失效方式是静默的：把一个 type 加进这里就等于宣称「有人处理它」，而某个渲染方其实没有
#: 分支 → 该 type 被原样当正文吐出来，**零报错**。拆开后测试可以机器核对
#: 「并集相等 + 两两不相交」。
#:
#: ⚠️ **Python 侧只消费本集合**（`is_known_type`），另几个是**镜像声明** —— 产物导出
#: （`render_blocks_html`）对任何 type 都一视同仁地包 `md-block`，不认识的在 CSS 层退化成
#: 普通内容。保留它们是为了让「并集 == 各族之和」这条断言在**两侧都成立**。
#:
#: ⚠️ 与前端 `markdown-scan.ts` 的同名集合**必须同源**，由
#: `frontend/src/test/fixtures/limo-blocks.json` 的 `meta` 逐族钉住。
KNOWN_BLOCK_TYPES = ARTIFACT_BLOCK_TYPES | CHAT_BLOCK_TYPES | SYNTHESIS_BLOCK_TYPES

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


#: 证据块类型名（`SYNTHESIS_BLOCK_TYPES` 的唯一成员）。
EVIDENCE_BLOCK_TYPE = "evidence"

#: 线性化后追加到文末的附录节标题。
#:
#: ⚠️ 与原位 `[证据 N]` **成对**：读者顺着 `[证据 3]` 往下翻能找到 `**证据 3**`。
EVIDENCE_APPENDIX_HEADING = "## 证据附录"

#: 合成产物里的文档锚点（`[文档 2]`）—— 用来给附录条目标出「这条证据来自哪几篇」。
_DOC_ANCHOR_RE = re.compile(r"\[文档\s*(\d+)\]")


def _evidence_source_note(text: str) -> str:
    """从一段文本里提取文档锚点，拼成 `文档 2、文档 5`；提不到返回空串。

    ⚠️ 提不到**不是失败**：模型可能整段都没写锚点。空串只是「不给来源标注」，
    条目照常收录。
    """
    nums = sorted({int(n) for n in _DOC_ANCHOR_RE.findall(text)})
    return "、".join(f"文档 {n}" for n in nums)


def _nearest_doc_anchors(lines: list[str]) -> str:
    """证据块**之前最近一段正文**（回溯到上一个空行为止）里的文档锚点。

    为什么要有这一步：模型把来源写在**论断旁**（`……这一结论 [文档 2]`）比写进证据块
    自然得多，而块内写的是方法 / 样本 / 局限。只看块内会让附录条目标题几乎永远是空的。
    """
    buf: list[str] = []
    for line in reversed(lines):
        if not line.strip():
            if buf:
                break
            continue
        buf.append(line)
    return _evidence_source_note("\n".join(reversed(buf)))


def linearize_evidence(md: str) -> str:
    """把 `evidence` 围栏**线性化**：原位留 `[证据 N]`，内容按序搬到文末 `## 证据附录`。

    这是「导出与预览刻意不同」的**唯一落点**（用户 2026-10-18 拍板）：预览里证据是可
    展开的 `<details>`，而任何导出格式（md / 纯文本 / HTML / Word / PDF）都没有交互，
    折叠内容在 md 里更是直接消失。线性化把「按需展开」降级成「原位占位 + 文末附录」，
    于是**任何格式都读得到依据**，且正文的连续阅读不被来源信息打断。

    矩阵（同一份写进 `docs/export-pipeline.md` 与前端 `EvidenceBlock` 注释）::

        | 维度          | 预览            | 导出                        | 允许不同 |
        | 内容字符      | 全在 DOM        | 全在文件                    | 否       |
        | H2 节数与顺序 | 原文            | 原文 + 文末「证据附录」     | 允许     |
        | 证据位置      | 原位 details    | 原位 [证据 N] + 附录        | 允许     |
        | 编号          | 无              | 全局顺序编号（导出派生）    | 允许     |
        | 协议标记行    | 不显示          | 不显示                      | 否       |
        | 交互          | 可折叠          | 不可折叠                    | 必然不同 |

    ⚠️ **无证据块时原样返回入参 `md` 本身** —— 不返回规范化后的中间副本（CRLF 会被改，
    「导出与改造前逐字节等价」当场失败）。有证据块时才走规范化（要逐行重建）。
    ⚠️ **幂等**：已线性化的文本里没有围栏，再调用一次命中上面那条早返回。
    ⚠️ **未闭合块不编号**：开标记已剥（零残留），块内内容当普通正文留在原位。
    ⚠️ **空壳块不编号**：没有内容就不占号（否则附录里出现空条目，编号与内容错位）。
    ⚠️ **代码围栏内的假标记不处理**（与 `parse_blocks` 同一份围栏感知）。
    ⚠️ **顺序不能反**：先 `linearize_evidence`、再 `strip_blocks`。反过来的话围栏已被
       剥掉，附录永远不生成 —— 而两边都「跑成功了」，零报错。
    ⚠️ **绝不塞进 `strip_blocks`**：它同时被合成输入（`latest_notes_text`）、对话导出、
       字数统计消费；放进去会让合成把附录当成原料再喂一遍。
    """
    if EVIDENCE_BLOCK_TYPE not in md:
        return md

    out: list[str] = []
    #: `(块内正文, 来源标注)` —— 来源优先取块内锚点，其次取前一段正文的锚点。
    collected: list[tuple[str, str]] = []
    ev_lines: list[str] = []
    in_ev = False
    in_fence = False

    for raw in _normalize(md).split("\n"):
        # ⚠️ 围栏判定排在标记判定之前，且对块内同样生效 —— 与 `parse_blocks` 同款。
        if FENCE_RE.match(raw):
            in_fence = not in_fence
            (ev_lines if in_ev else out).append(raw)
            continue
        if in_fence:
            (ev_lines if in_ev else out).append(raw)
            continue

        if in_ev:
            if closes_block(raw):
                body = "\n".join(ev_lines).strip("\n")
                if body.strip():
                    note = _evidence_source_note(body) or _nearest_doc_anchors(out)
                    collected.append((body, note))
                    out.append(f"[证据 {len(collected)}]")
                elif out and out[-1].strip():
                    # 空壳：不留占位、不编号。只有「上一行是正文」时才补一个空行防段落
                    # 粘连 —— 无条件补的话，块前后本就各有一个空行，正文里会留下
                    # 连续三个空行（语义无害但让产物多出一处肉眼可见的怪缝）。
                    out.append("")
                in_ev = False
                ev_lines = []
                continue
            # 块内嵌套 / 畸形的标记行 → 剥掉（与 `parse_blocks` 同口径）。
            if MARKER_LINE_RE.match(raw):
                continue
            ev_lines.append(raw)
            continue

        m = match_open(raw)
        if m is not None and m.group("type") == EVIDENCE_BLOCK_TYPE:
            in_ev = True
            ev_lines = []
            continue
        # ⚠️ 其余标记行（产物侧其余块、`card` 节修饰符、未知版本）**原样保留** ——
        # 它们要照常走 `render_blocks_html` 的块渲染，这里只消解证据块。
        out.append(raw)

    if not collected:
        # 只有未闭合块 / 围栏内假标记 → 什么都没改，原样返回入参（含 CRLF）。
        return md

    if in_ev:
        # 末尾未闭合：内容降级为普通正文（开标记已在上面被丢弃）。
        out.extend(ev_lines)

    body_text = "\n".join(out).rstrip("\n")

    # 用户自己写了一节叫「证据附录」时，不产生两个同名 H2 —— 改用「（续）」，内容不丢。
    heading = EVIDENCE_APPENDIX_HEADING
    if re.search(r"^##\s+证据附录\s*$", body_text, re.MULTILINE):
        heading = EVIDENCE_APPENDIX_HEADING + "（续）"

    appendix: list[str] = []
    for i, (body, note) in enumerate(collected, 1):
        appendix.append(f"**证据 {i}**" + (f"（{note}）" if note else ""))
        appendix.append("")
        appendix.append(body)
        appendix.append("")

    return "\n\n".join([body_text, heading, "\n".join(appendix).rstrip("\n")]) + "\n"


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
