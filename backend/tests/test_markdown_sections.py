"""H2 切分器（后端）—— 表驱动读**跨语言共享 fixture**。

前端 `frontend/src/lib/markdown-sections.ts::splitByH2` 是同一份判据的 JS 实现。
两份实现**必然存在**（预览要 JS 的、导出要 Python 的），这份 JSON 是它们唯一的
一致性证据 —— 「两边写得像」不是证据。`frontend/src/test/markdownSections.test.ts`
读同一份文件、跑本文件**逐字对应的**断言。

⚠️ 读不到的后果要说清楚：路径写错的话这里会直接 `FileNotFoundError`（不是静默
跳过）。**刻意不写 `pytest.skip`** —— 跳过的断言和没有的断言在报告上长得一样。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.markdown_sections import iter_headings, split_by_h2

#: ⚠️ 从本文件反推仓库根，**不写死绝对路径**（写死的话换机器 / CI 上就找不到，
#: 而找不到的表现是「这组用例消失了」—— 正是要防的静默）。
FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "frontend" / "src" / "test" / "fixtures" / "h2-sections.json"
)

FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
CASES = FIXTURE["cases"]


def test_fixture_可读且用例没被删空():
    """防「用例被删空后整组测试静默变成空跑」—— 参数化到空列表时 pytest 报 0 个
    用例（exit 5），看起来像失败也可能被忽略。这里先正面断言它有内容。"""
    assert FIXTURE_PATH.is_file(), f"共享 fixture 不存在：{FIXTURE_PATH}"
    assert len(CASES) >= 8, f"用例太少（{len(CASES)}），可能被误删"


def test_用例覆盖到那几类关键行为():
    """名称级守卫：关键行为一条都不能少，否则本文件会「绿着失去意义」。"""
    names = "\n".join(c["name"] for c in CASES)
    for kw in ("围栏", "无 H2", "引言", "CRLF", "末尾", "内部"):
        assert kw in names, f"fixture 缺少「{kw}」类用例"


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_与前端同形(case):
    """逐字段比对 —— 断言的是「前端那份实现会得到什么」，不是「后端自己觉得对」。"""
    expected = (case["preamble"], [(s["heading"], s["body"]) for s in case["sections"]])
    assert split_by_h2(case["md"]) == expected


# ── 结构不变量（与前端 markdownSections.test.ts 的第二节对称）──────

def test_无_H2_时原样返回而不是丢内容():
    """对照：返回 `("", [])` 会让**整篇内容凭空消失**（长文页空白、零报错）。"""
    md = "只有一段话\n第二段\n"
    preamble, sections = split_by_h2(md)
    assert sections == []
    assert "只有一段话" in preamble and "第二段" in preamble


def test_heading_保留井号前缀():
    """卡片内把 `heading + "\\n" + body` 原样交给渲染器 —— 标题留在里面，
    因为卡片头就是那个 `##`。剥掉前缀会让渲染器认不出它是标题。"""
    _preamble, sections = split_by_h2("## 甲\n内容\n")
    assert sections[0][0] == "## 甲"


def test_只切_H2_不认_H1_与_H3():
    preamble, sections = split_by_h2("# 大标题\n\n### 三级\n内容\n")
    assert sections == []
    assert "# 大标题" in preamble


def test_拼回去内容零增减():
    md = "## 甲\n内容一\n\n## 乙\n内容二\n\n## 丙\n内容三\n"
    preamble, sections = split_by_h2(md)
    joined = "\n\n".join(
        [x for x in [preamble, *(f"{h}\n{b}" for h, b in sections)] if x != ""]
    )
    for chunk in ("## 甲", "内容一", "## 乙", "内容二", "## 丙", "内容三"):
        assert chunk in joined


# ── iter_headings 自身（统计脚本的唯一消费点）────────────────────

def test_iter_headings_不被围栏内的假标题骗到():
    """⚠️ 这是**自证有区分力**：把围栏判断拿掉，本断言立刻红。

    项目病史：`extractHeadings` 曾把代码块里的 `## ` 当成标题，产出「点了没反应
    的幽灵目录项」。同一个坑在切卡上的形态是「一张内容是代码的幽灵卡片」。
    """
    md = "## 真\n\n```\n## 假\n```\n\n~~~\n## 也假\n~~~\n"
    got = [t for _n, _lv, t in iter_headings(md)]
    assert got == ["真"]


def test_iter_headings_级别过滤生效():
    md = "# 一\n## 二\n### 三\n"
    assert [t for _n, _lv, t in iter_headings(md, 2, 2)] == ["二"]
    assert [t for _n, _lv, t in iter_headings(md, 1, 3)] == ["一", "二", "三"]
