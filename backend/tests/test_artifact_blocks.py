"""`limo-block` 标记解析器（后端）—— 表驱动读**跨语言共享 fixture**。

前端 `frontend/src/lib/markdown-markers.ts` 是同一份判据的 JS 实现。两份实现**必然存在**
（预览要 JS 的、导出要 Python 的），这份 JSON 是它们唯一的一致性证据 —— 「两边写得像」
不是证据。`frontend/src/test/markdownMarkers.test.ts` 读同一份文件、跑逐字对应的断言。

⚠️ 读不到的后果要说清楚：路径写错的话这里会直接 `FileNotFoundError`（不是静默跳过）。
**刻意不写 `pytest.skip`** —— 跳过的断言和没有的断言在报告上长得一样。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.artifact_blocks import (
    ARTIFACT_BLOCK_TYPES,
    CHAT_BLOCK_TYPES,
    KNOWN_BLOCK_TYPES,
    PROTOCOL_VERSION,
    has_blocks,
    is_known_type,
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


def test_fixture_可读且用例没被删空():
    """防「用例被删空后整组测试静默变成空跑」—— 参数化到空列表时 pytest 报 0 个
    用例（exit 5），看起来像失败也可能被忽略。这里先正面断言它有内容。"""
    assert FIXTURE_PATH.is_file(), f"共享 fixture 不存在：{FIXTURE_PATH}"
    assert len(CASES) >= 11, f"用例太少（{len(CASES)}），可能被误删"
    assert len(H2_CASES) >= 2, f"h2 用例太少（{len(H2_CASES)}），可能被误删"


def test_用例覆盖到那几类关键行为():
    """名称级守卫：关键行为一条都不能少，否则本文件会「绿着失去意义」。"""
    names = "\n".join(c["name"] for c in CASES)
    for kw in ("无标记", "未闭合", "未知协议版本", "围栏内", "不算闭合", "CRLF"):
        assert kw in names, f"fixture 缺少「{kw}」类用例"


# ── 跨语言守卫：白名单与协议版本 ────────────────────────────────

def test_已知类型白名单与前端同源():
    """⚠️ 加一个块类型时改 fixture + 两侧各一处，**漏改任一侧即红**。
    这正是「同源」从口号变成机制的地方。"""
    assert KNOWN_BLOCK_TYPES == frozenset(FIXTURE["meta"]["knownTypes"])


def test_协议版本与前端同源():
    assert PROTOCOL_VERSION == FIXTURE["meta"]["protocolVersion"]


def test_三个集合的关系与_fixture_同源():
    """`ARTIFACT ∪ CHAT == KNOWN`、两者**不相交**、各等于 fixture 的子集。

    ⚠️ 这条挡住的是「加了 type 却漏写渲染器」—— 那种失效**零报错**：协议认识它，
    某一侧把它当普通正文吐出来，看起来像「模型没写标记」（`limo-blocks.json` 的
    `meta` 注释里写着同一条纪律）。
    """
    meta = FIXTURE["meta"]
    assert ARTIFACT_BLOCK_TYPES == frozenset(meta["artifactTypes"])
    assert CHAT_BLOCK_TYPES == frozenset(meta["chatTypes"])
    assert KNOWN_BLOCK_TYPES == ARTIFACT_BLOCK_TYPES | CHAT_BLOCK_TYPES
    assert not (ARTIFACT_BLOCK_TYPES & CHAT_BLOCK_TYPES)
    # 两侧的类型都在协议白名单里（`is_known_type` 是渲染方唯一的查询入口）
    for t in KNOWN_BLOCK_TYPES:
        assert is_known_type(t), t


def test_fixture_覆盖了两个新类型():
    """名称级守卫：光有集合常量不算覆盖 —— 新类型必须**至少有一条解析用例**，
    否则「加进白名单」与「真的能解析」之间没有任何证据。"""
    names = "\n".join(c["name"] for c in CASES)
    for kw in ("card-group", "follow-up"):
        assert kw in names, f"fixture 缺少 {kw} 的用例"


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
    expected = (case["preamble"], [(s["heading"], s["body"]) for s in case["sections"]])
    assert split_by_h2(case["md"]) == expected


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
