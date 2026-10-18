"""`limo-block` 标记解析器（后端）—— 表驱动读**跨语言共享 fixture**。

前端 `frontend/src/lib/markdown-markers.ts` 是同一份判据的 JS 实现。两份实现**必然存在**
（预览要 JS 的、导出要 Python 的），这份 JSON 是它们唯一的一致性证据 —— 「两边写得像」
不是证据。`frontend/src/test/markdownMarkers.test.ts` 读同一份文件、跑逐字对应的断言。

⚠️ 读不到的后果要说清楚：路径写错的话这里会直接 `FileNotFoundError`（不是静默跳过）。
**刻意不写 `pytest.skip`** —— 跳过的断言和没有的断言在报告上长得一样。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.core.artifact_blocks import (
    ARTIFACT_BLOCK_TYPES,
    CHAT_BLOCK_TYPES,
    EVIDENCE_APPENDIX_HEADING,
    EVIDENCE_BLOCK_TYPE,
    KNOWN_BLOCK_TYPES,
    PROTOCOL_VERSION,
    SECTION_CARD_TYPE,
    SYNTHESIS_BLOCK_TYPES,
    has_blocks,
    is_known_type,
    is_section_card,
    linearize_evidence,
    match_open,
    parse_blocks,
    strip_blocks,
)
from app.services.markdown_sections import iter_headings, split_by_h2

#: ⚠️ 从本文件反推仓库根，**不写死绝对路径**（写死的话换机器 / CI 上就找不到，
#: 而找不到的表现是「这组用例消失了」—— 正是要防的静默）。
FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "frontend" / "src" / "test" / "fixtures" / "limo-blocks.json"
)

FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
CASES = FIXTURE["cases"]
H2_CASES = FIXTURE["h2Cases"]

#: 证据线性化的共享 fixture —— 与 `frontend/src/test/markdownMarkers.test.ts` 同源。
LINEARIZE_FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "frontend" / "src" / "test" / "fixtures" / "evidence-linearize.json"
)
LINEARIZE_FIXTURE = json.loads(LINEARIZE_FIXTURE_PATH.read_text(encoding="utf-8"))
LINEARIZE_CASES = LINEARIZE_FIXTURE["cases"]


def test_fixture_可读且用例没被删空():
    """防「用例被删空后整组测试静默变成空跑」—— 参数化到空列表时 pytest 报 0 个
    用例（exit 5），看起来像失败也可能被忽略。这里先正面断言它有内容。"""
    assert FIXTURE_PATH.is_file(), f"共享 fixture 不存在：{FIXTURE_PATH}"
    assert len(CASES) >= 20, f"用例太少（{len(CASES)}），可能被误删"
    assert len(H2_CASES) >= 6, f"h2 用例太少（{len(H2_CASES)}），可能被误删"
    assert LINEARIZE_FIXTURE_PATH.is_file(), f"共享 fixture 不存在：{LINEARIZE_FIXTURE_PATH}"
    assert len(LINEARIZE_CASES) >= 10, f"线性化用例太少（{len(LINEARIZE_CASES)}）"


def test_用例覆盖到那几类关键行为():
    """名称级守卫：关键行为一条都不能少，否则本文件会「绿着失去意义」。

    ⚠️ 后两个关键字（`steps` / `timeline`）**不是同类的行为用例** —— 它们是「块内
    列表结构原样保留」的形态判据（预览与导出两侧都靠 `<ul>` / `<ol>` 区分这两个
    类型，见 `blocks.css`）。放在这条里是因为判据的形式相同（名称级），而不是说
    它们与「未闭合 / 围栏内」属于同一类降级行为。
    """
    names = "\n".join(c["name"] for c in CASES)
    for kw in ("无标记", "未闭合", "未知协议版本", "围栏内", "不算闭合", "CRLF",
               "steps", "timeline"):
        assert kw in names, f"fixture 缺少「{kw}」类用例"
    # ⚠️ `h2Cases` 单独点名：`card` 是本轮唯一的**节级**修饰符，它那几条用例
    # （成卡 / 隔空行 / 写在标题之前 / 与块同节）是「节修饰符不是块」这件事的全部证据。
    h2_names = "\n".join(c["name"] for c in H2_CASES)
    for kw in ("card", "之前"):
        assert kw in h2_names, f"h2Cases 缺少「{kw}」类用例"


# ── 跨语言守卫：白名单与协议版本 ────────────────────────────────

def test_已知类型白名单与前端同源():
    """⚠️ 加一个块类型时改 fixture + 两侧各一处，**漏改任一侧即红**。
    这正是「同源」从口号变成机制的地方。"""
    assert KNOWN_BLOCK_TYPES == frozenset(FIXTURE["meta"]["knownTypes"])


def test_协议版本与前端同源():
    assert PROTOCOL_VERSION == FIXTURE["meta"]["protocolVersion"]


def test_节级修饰符与_fixture_同源_且不在块白名单里():
    """`card` 是**第三节集合**（节级修饰符，2026-10-11）—— 与前端 `markdown-scan.ts`
    同源，且**刻意不进 `knownTypes`**。

    ⚠️ 两个方向各自对应一种静默失效：
    · 少了前半条 → 前后端 `card` 拼写漂移（模型写的是 `card`，只有一侧认得它）。
    · 少了后半条 → `card` 被并进 `knownTypes`，于是「包不包强调块外壳」的判定
      （`is_known_type`）认为它算 —— 而 card 根本不开块。两种判定混在一起零报错，
      后果是标题被当成块内容吞掉。
    """
    assert list(FIXTURE["meta"]["sectionTypes"]) == [SECTION_CARD_TYPE]
    assert SECTION_CARD_TYPE not in KNOWN_BLOCK_TYPES


def test_三个集合的关系与_fixture_同源():
    """`ARTIFACT ∪ CHAT ∪ SYNTHESIS == KNOWN`、两两**不相交**、各等于 fixture 的子集。

    ⚠️ 这条挡住的是「加了 type 却漏写渲染器」—— 那种失效**零报错**：协议认识它，
    某一侧把它当普通正文吐出来，看起来像「模型没写标记」（`limo-blocks.json` 的
    `meta` 注释里写着同一条纪律）。
    """
    meta = FIXTURE["meta"]
    assert ARTIFACT_BLOCK_TYPES == frozenset(meta["artifactTypes"])
    assert CHAT_BLOCK_TYPES == frozenset(meta["chatTypes"])
    assert SYNTHESIS_BLOCK_TYPES == frozenset(meta["synthesisTypes"])
    assert KNOWN_BLOCK_TYPES == (
        ARTIFACT_BLOCK_TYPES | CHAT_BLOCK_TYPES | SYNTHESIS_BLOCK_TYPES
    )
    assert not (ARTIFACT_BLOCK_TYPES & CHAT_BLOCK_TYPES)
    # ⚠️ 合成侧与产物侧**必须不相交**：`evidence` 若被并进 `ARTIFACT_BLOCK_TYPES`，
    # 单篇的 `get_markdown_block_format()` 就会被 `test_block_prompt.py` 追着教证据块
    # （而单篇材料没有跨文档分歧可写）—— 那条断言是产品边界的守卫，不只是集合卫生。
    assert not (ARTIFACT_BLOCK_TYPES & SYNTHESIS_BLOCK_TYPES)
    assert not (CHAT_BLOCK_TYPES & SYNTHESIS_BLOCK_TYPES)
    # 两侧的类型都在协议白名单里（`is_known_type` 是渲染方唯一的查询入口）
    for t in KNOWN_BLOCK_TYPES:
        assert is_known_type(t), t


def test_每个白名单类型都至少有一条解析用例():
    """名称级守卫的**集合驱动**版本：光有集合常量不算覆盖 —— 白名单里每个类型都必须
    至少有一条解析用例，否则「加进白名单」与「真的能解析」之间没有任何证据。

    ⚠️ 判据从 `segments[].type` **派生**，而不是人肉维护一份「新类型名单」：手写名单是
    同一件事实的第二份副本（模式 #54），下次加 type 忘了改它，这条守卫就会**绿着失去
    意义** —— 而它本该正是「加了 type 却忘了写用例」的那道防线。
    """
    covered = {s["type"] for c in CASES for s in c["segments"] if "type" in s}
    missing = sorted(KNOWN_BLOCK_TYPES - covered)
    assert not missing, f"白名单里这些类型没有任何 fixture 用例：{missing}"


def test_对话侧类型在产物侧只剥标记行_结构原样保留():
    """产物侧不认识 `card-group`，但**零丢失**地处理它：`###` 结构原样交给 Markdown
    渲染器（`render_blocks_html` 对任何 type 都一视同仁地包 `md-block`，CSS 层匹配不到
    `card-group` 就退化成普通内容）。

    ⚠️ 与「解析器对 type 无感知」这条设计同源 —— 若解析器按白名单吞内容，
    产物侧碰到对话类型就会**静默丢一段正文**。
    """
    md = ('<!-- limo-block:v1 type="card-group" -->\n'
          "### 甲\n内容一\n\n### 乙\n内容二\n<!-- /limo-block -->")
    assert strip_blocks(md) == "### 甲\n内容一\n\n### 乙\n内容二"
    assert parse_blocks(md) == [
        {"kind": "block", "type": "card-group", "content": "### 甲\n内容一\n\n### 乙\n内容二"}
    ]


# ── 表驱动：与前端逐字段同形 ────────────────────────────────────

@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_parse_blocks_与前端同形(case):
    """逐字段比对 —— 断言的是「前端那份实现会得到什么」，不是「后端自己觉得对」。
    fixture 的 segment 形状（kind / type / content）就是为这次比对定的。"""
    assert parse_blocks(case["md"]) == case["segments"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_strip_blocks_与前端同形(case):
    assert strip_blocks(case["md"]) == case["stripped"]


@pytest.mark.parametrize("case", H2_CASES, ids=[c["name"] for c in H2_CASES])
def test_split_by_h2_跳过标记块(case):
    # ⚠️ `card` 用 `.get(..., False)` 归一：带 card 的用例（`limo-blocks.json::h2Cases`
    # 里那四条）就比 card，不带的默认 False —— 而不是给每条用例手写一遍 `"card": False`
    # （那是把「无标记 ⇒ 不成卡」这条推导抄 6 遍，抄错了反而是新缺陷）。
    expected = (
        case["preamble"],
        [(s["heading"], s["body"], s.get("card", False)) for s in case["sections"]],
    )
    assert split_by_h2(case["md"]) == expected


def test_对照实验_两个判据不是同一个函数的别名():
    """⚠️ 这是上面那些用例**自证有区分力**的方式。

    若把 `is_section_card` 实现成 `match_open(line) is not None`（读起来等价，也确实
    能通过不少断言），card 就会开块 → 扫描进入块内状态 → **标题与整节内容被跳过**。
    这里正反两面都断，别名实现在第一条就红。
    """
    card = '<!-- limo-block:v1 type="card" -->'
    alert = '<!-- limo-block:v1 type="alert" -->'
    assert match_open(card) is None
    assert is_section_card(card) is True
    assert match_open(alert) is not None
    assert is_section_card(alert) is False


# ── 结构不变量（与前端 markdownMarkers.test.ts 的第二节对称）──────

def test_无标记时逐字返回同一对象():
    """对照：把「无标记」也拖进「规范化 + 逐行重拼」的实现 → CRLF 产物被静默改成 LF，
    而这是所有旧产物的必经路径，输出与输入不再逐字相同。"""
    md = "普通文章\r\n\r\n没有任何标记"
    assert strip_blocks(md) is md
    assert parse_blocks(md) == [{"kind": "text", "content": "普通文章\n\n没有任何标记"}]


def test_未闭合降级后与从未写过标记逐字相同():
    """⚠️ 本协议最重要的一条不变量：模型少写一行闭标记，**内容一字不丢**。
    对照：把 `block_lines` 一起吞掉的实现 → 读者永久看不到那段结论，且产物上看不出
    少了什么（预览里只是「少了点什么」，导出里也没有痕迹）。"""
    with_marker = '正文一\n\n<!-- limo-block:v1 type="key-takeaway" -->\n块内文字\n块内第二行'
    never = "正文一\n\n块内文字\n块内第二行"
    assert strip_blocks(with_marker) == never


def test_块内容是原文不_trim_也不剥加粗():
    """块内渲染必须与块外走同一条 Markdown 路径 —— 任何预处理都会让两者分叉。"""
    segs = parse_blocks('<!-- limo-block:v1 type="key-takeaway" -->\n\n  **甲**  \n\n<!-- /limo-block -->')
    assert segs == [{"kind": "block", "type": "key-takeaway", "content": "\n  **甲**  \n"}]


def test_strip_blocks_幂等():
    for case in CASES:
        once = strip_blocks(case["md"])
        assert strip_blocks(once) == once


def test_has_blocks_是子串判定():
    """误报只多走一次慢路径（代价可忽略），漏报才是 bug —— 子串判定不会漏。"""
    assert has_blocks("提到 limo-block 这个词") is True
    assert has_blocks("普通正文") is False


# ── iter_headings 的标记块守卫（自证有区分力）────────────────────

def test_iter_headings_不被标记块内的假标题骗到():
    """⚠️ 把 `match_open` / `closes_block` 的判断拿掉，本断言立刻红。

    同一个坑的两个形态：围栏内是「点了没反应的幽灵目录项」，标记块内是
    「TOC 多一项，且切卡时切出内容是半截强调块的卡」。
    """
    md = (
        '## 真\n\n<!-- limo-block:v1 type="key-takeaway" -->\n'
        "## 块内假标题\n<!-- /limo-block -->\n\n## 真二\n"
    )
    assert [t for _n, _lv, t in iter_headings(md, 2, 2)] == ["真", "真二"]


# ── 证据块线性化（导出专用，与前端 markdownMarkers.test.ts 同源）─────────

def test_证据块常量与_fixture_同源():
    """`evidence` 是 `SYNTHESIS_BLOCK_TYPES` 的**唯一**成员，附录标题两侧同字。

    ⚠️ 附录标题是跨语言的字面量（前端 `EVIDENCE_APPENDIX_HEADING`、后端同常量）——
    写死两份就是模式 #54：改一侧后另一侧静默不同，症状是「同一个文档，md 导出与
    前端复制出来的附录节名不一样」，两个产物都看起来正常。
    """
    meta = LINEARIZE_FIXTURE["meta"]
    assert SYNTHESIS_BLOCK_TYPES == frozenset({EVIDENCE_BLOCK_TYPE})
    assert meta["blockType"] == EVIDENCE_BLOCK_TYPE
    assert meta["appendixHeading"] == EVIDENCE_APPENDIX_HEADING


@pytest.mark.parametrize("case", LINEARIZE_CASES, ids=[c["name"] for c in LINEARIZE_CASES])
def test_linearize_与前端同形(case):
    """逐字节比对 —— 断言的是「前端那份实现会得到什么」，不是「后端自己觉得对」。

    ⚠️ 这一组用例里**必须**有「无证据块 + CRLF」那条：线性化的零回归承诺是
    「原样返回入参」，而任何走规范化中间副本的实现都会把 CRLF 改成 LF，
    对全部历史产物生效却零报错。
    """
    assert linearize_evidence(case["md"]) == case["linearized"]


@pytest.mark.parametrize("case", LINEARIZE_CASES, ids=[c["name"] for c in LINEARIZE_CASES])
def test_linearize_幂等(case):
    """对**已线性化**的文本再调一次必须逐字节不变。

    ⚠️ 幂等不是洁癖：`linearize_evidence` 会在四个导出入口各调一次，而 `_collect_doc_products`
    的结果还可能被批量导出再消费一遍。不幂等的实现会追加第二个「## 证据附录」。
    """
    once = linearize_evidence(case["md"])
    assert linearize_evidence(once) == once


@pytest.mark.parametrize("case", LINEARIZE_CASES, ids=[c["name"] for c in LINEARIZE_CASES])
def test_linearize_后再_strip_零残留(case):
    """导出链是 `strip_blocks(linearize_evidence(md))`（顺序不能反）。

    ⚠️ 这条钉的是**组合后**的性质：任意一份合成产物走完导出链后，读者可见正文里
    不得残留任何协议标记行。单个函数各自正确、组合起来仍可能漏（例如未闭合块的
    开标记由谁剥）。

    ⚠️ **含代码围栏的用例反向断言**：围栏内是用户内容，`strip_blocks` 刻意不碰它
    （「围栏内零触碰」是协议的第一条不变量）。在那里断言「不得残留」等于要求实现
    去改写用户代码块 —— 方向反了。⚠️ 写成 `pytest.skip` 不行：跳过的断言与没有的
    断言在报告上长得一样，而这条恰恰要证明「剥标记的射程**止于**围栏边界」。
    """
    out = strip_blocks(linearize_evidence(case["md"]))
    if "```" in case["md"]:
        assert "<!-- limo-block" in out, "围栏内的假标记被改写了 —— 违反「围栏内零触碰」"
        assert "<!-- /limo-block" in out, case["name"]
        return
    assert "<!-- limo-block" not in out, case["name"]
    assert "<!-- /limo-block" not in out, case["name"]


def _evidence_blk(body: str) -> str:
    return f'<!-- limo-block:v1 type="evidence" -->\n{body}\n<!-- /limo-block -->'


def test_线性化编号与出现顺序双向对齐且无孤儿():
    """正文的 `[证据 N]` 与附录的 `**证据 N**` 必须**同序同集**。

    ⚠️ 只断「编号从 1 开始递增」是不够的：那样「正文有 [证据 3]、附录只有 1~2」照样绿，
    而读者顺着 [证据 3] 往下翻什么也找不到 —— 这正是「编号由结构派生」这条设计
    唯一可能失效的方式。
    """
    out = linearize_evidence(
        f"前言 [文档 1]。\n\n{_evidence_blk('甲')}\n\n"
        f"{_evidence_blk('乙')}\n\n{_evidence_blk('丙')}\n"
    )
    in_body = re.findall(r"\[证据 (\d+)\]", out)
    in_appendix = re.findall(r"^\*\*证据 (\d+)\*\*", out, re.MULTILINE)
    assert in_body == ["1", "2", "3"]
    assert in_appendix == in_body, f"正文编号 {in_body} 与附录条目 {in_appendix} 不同源"
