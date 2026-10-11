"""Markdown 按 H2 切分 —— 卡片骨架的**后端切分器**。

前端 `frontend/src/lib/markdown-sections.ts::splitByH2` 是同一份判据的 JS 实现。
两份**必须同构**，由共享 fixture 钉住（`frontend/src/test/fixtures/h2-sections.json`，
两侧各读同一文件、各跑各的断言）—— 「写得像」不算机制，本项目历史上最高发的一类
缺陷就是「同一份判据住在两处、其中一处悄悄漂移」。

⚠️ **围栏感知是硬要求，不是优化**：代码块里的 `## ` 被当成标题，会切出一张内容是
代码的幽灵卡片。前端 `extractHeadings` 已经踩过同一个坑（产出了「点了没反应的幽灵
目录项」），所以这里与它共用同一套扫描规则。

本模块**纯函数、零第三方依赖**（`re` + `app.core.artifact_blocks`，后者同样只 import `re`），
因为三个调用方的东西很不一样：`routers/export.py`（HTML 拼装）、
`services/docx_export.py`（Word 块渲染）、`scripts/check-artifact-structure.py`
（只读统计 —— 它连 FastAPI 都不该拉起来）。

⚠️ **`FENCE_RE` 与标记判据一律从 `artifact_blocks` import**，不在这里再定义一份 ——
「这一行是什么」的分类只该有一处真相源；两份扫描规则的漂移是静默的（不报错，
只是切出来的卡片数量对不上）。
"""

from __future__ import annotations

import re
from typing import Iterator

from app.core.artifact_blocks import FENCE_RE, closes_block, is_section_card, match_open

#: ATX 标题：`#{1,6}` + 至少一个空白 + 文本。
#: ⚠️ 与前端 `markdown-scan.ts::HEADING_RE` 同构。
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def iter_headings(md: str, min_level: int = 1, max_level: int = 6
                  ) -> Iterator[tuple[int, int, str]]:
    """产出 `(行号, 级别, 标题文本)`，**跳过围栏内与标记块内的假标题**。

    行号是 0 基、`md.split("\\n")` 的下标，可直接用来切 `lines` 列表。

    这是「哪些行是标题」的**唯一真相源** —— `split_by_h2` 与统计脚本都从这里取，
    不各自再写一遍正则（两份扫描规则的漂移是静默的：不报错，只是切出来的卡片
    数量对不上）。
    """
    in_fence = False
    in_block = False
    for lineno, raw in enumerate(md.split("\n")):
        line = raw[:-1] if raw.endswith("\r") else raw
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        # ⚠️ **标记块内的 `##` 不是文档结构** —— 它属于「内容块」内部（强调块 / 内容卡片
        # 都算）。在这里切它会切出一张内容是半截块的卡（TOC 还会多一个点了没反应的幽灵项）。
        # 与前端 `markdown-scan.ts::scanHeadingLines` 同构。
        if in_block:
            if closes_block(line):
                in_block = False
            continue
        if match_open(line):
            in_block = True
            continue
        m = HEADING_RE.match(line)
        if not m:
            continue
        level = len(m.group(1))
        if min_level <= level <= max_level:
            yield lineno, level, m.group(2)


#: 分隔线行（CommonMark thematic break：三个以上 `-` / `*` / `_`，可带空白间隔）。
#: ⚠️ 用反向引用而不是把三类字符各写一遍 —— 三份规则本身就该是同一份。
_THEMATIC_BREAK_RE = re.compile(r"^\s{0,3}([-*_])\s*(?:\1\s*){2,}$")


def _trim_body(lines: list[str], strip_break: bool) -> str:
    """去首尾空行；`strip_break` 为真时再把**尾部的分隔线行**（及其上方空行）一并去掉。

    ⚠️ **`strip_break` 由「这一节是否成卡」决定**（2026-10-11）：分隔线的功能是「把这一节
    与下一节分开」，而**成卡的节已经有了卡的下边界**，那条 `---` 就只剩「卡片底部多一条线」。
    未标记的节没有卡边界，`---` 仍是它唯一的分隔手段 —— 一并剥掉会让「开了卡片模式，
    普通章节之间的分隔线却消失了」，而那是与骨架轴无关的损失。

    ⚠️ 与前端 `markdown-sections.ts::trimBody` **逐字同构**（同一份 fixture 钉住）。
    """
    end = len(lines)
    while end > 0 and lines[end - 1].strip() == "":
        end -= 1
    if strip_break:
        while end > 0 and _THEMATIC_BREAK_RE.match(lines[end - 1]):
            end -= 1
            while end > 0 and lines[end - 1].strip() == "":
                end -= 1
    start = 0
    while start < end and lines[start].strip() == "":
        start += 1
    return "\n".join(lines[start:end])


def split_by_h2(md: str) -> tuple[str, list[tuple[str, str, bool]]]:
    """按 H2（`## `）切成「前言 + 各节」。返回 `(preamble, [(heading, body, card), ...])`。

    · `heading` —— 原始标题行（**含 `## ` 前缀**）。调用方把它与 `body` 拼回
      （`f"{heading}\\n{body}"`）即是该节完整的 Markdown —— 标题留在里面，因为
      卡片头就是那个 `##`；
    · `body` —— 该节其余内容，已去首尾空行与**尾部分隔线**；
    · `card` —— 这一节是否被 `card` **节修饰符**标记（`<!-- limo-block:v1 type="card" -->`
      写在标题行之后）。⚠️ 标记行**已从 `body` 剥掉** —— 它不该流到渲染器。

    ⚠️ **切分永远按标题层级进行**，不因为有没有标记而变（未标记的节照样是一个
    `Section`，只是 `card` 为假）。导出侧要按节拼 HTML、TOC 要按节列条目，若「没标记的
    节根本不存在」，两处都得改 —— 于是判据收在 `card` 这一个字段里，其余形状不变。

    ⚠️ **`card` 只认「标题之后」**：写在标题**之前**的标记在 `iter_headings` 里对结构
    完全透明（见 `artifact_blocks.SECTION_CARD_TYPE`），于是它退化成「这一节不成卡、
    结构与内容一字不少」。宽容写在**降级**这一侧，不写在判据这一侧。

    ⚠️ **与前端 `splitByH2` 的返回值形状逐字段对齐**（同一份 fixture 逐条比对
    `preamble` 与 `sections` 的每个字段）。形状不同的话「两侧都读同一份 fixture」
    这句话就落不了地 —— 那正是「同源」最容易退化成口号的地方。

    ⚠️ **`heading` 不做行内装饰剥离**（`*Content-[mm:ss]` 之类）—— 那是前端
    `stripHeadingDecor` 的职责，在它那里已经有一份实现。这里再写一份就是同一个
    正则的第二个副本，正是本模块 docstring 警告的形态。

    边界：

    · **无 H2** → `(md, [])` —— 调用方据此回落「整篇」形态（**不丢内容**）；
    · **H2 之前的引言** → `preamble`（不丢弃，已去尾部空白）；
    · **H2 内的 H3/H4** → 留在该节的 `body` 里（卡片是容器，不是细分器）；
    · **CRLF** → 入口整体规范化（`\\r\\n` 与孤立 `\\r`），与前端一致。

    ⚠️ `preamble` 可能是**空串**（正文直接从 `## ` 开头）。判空用 `if preamble.strip()`，
    不要用 `if preamble:` —— 不过本函数已经去过尾空白，空的话就是空串。

    ⚠️ **为什么尾部分隔线要在这里剥**：`_NOTES_TASK` 第 6 条逐字要求「章节或话题之间用
    分割线（`---`）分隔」，于是产物里 `---` 会出现在**每个 H2 章节的末尾**。切成卡片后，
    章节间的分隔功能已由**卡片间距**承担，那条 `---` 只剩「每张卡底部多一条线」这一个
    效果。放在切分器（而非各渲染方）是因为前端预览与后端导出**都**要剥 —— 两处各写一遍
    就是「同源漂移」的经典入口（模式 #54）。**只剥尾部**：章节**内部**的 `---`
    （分隔话题）原样保留。
    """
    # ⚠️ 先整体规范化 CRLF：只在匹配时剥 `\r` 是不够的，正文的**每一行**都可能带尾 `\r`。
    # 行数不变，故 `iter_headings` 给出的行号在规范化前后一致。
    src = re.sub(r"\r\n?", "\n", md)
    lines = src.split("\n")
    h2 = [(lineno, text) for lineno, level, text in iter_headings(src, 2, 2)]
    if not h2:
        return src, []

    sections: list[tuple[str, str, bool]] = []
    for i, (start, _title) in enumerate(h2):
        end = h2[i + 1][0] if i + 1 < len(h2) else len(lines)
        body_lines = lines[start + 1:end]
        # 节修饰符 —— 标题行之后**第一个非空行**是 `card` 标记 → 这一节成卡，并剥掉该行。
        at = next((j for j, l in enumerate(body_lines) if l.strip() != ""), -1)
        card = at >= 0 and is_section_card(body_lines[at])
        if card:
            body_lines = body_lines[:at] + body_lines[at + 1:]
        sections.append((lines[start], _trim_body(body_lines, card), card))
    preamble = "\n".join(lines[:h2[0][0]]).rstrip()
    return preamble, sections
