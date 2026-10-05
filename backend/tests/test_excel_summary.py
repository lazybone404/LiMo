"""Excel 数据摘要的结构化路径（P1-07）。

缺陷：`_parse_markdown_table` 一开始就把 `{"name": "Sheet1"}` 塞进 `sheets`，而
`doc_excel.py:138` 的输出**每一份都以 `## Sheet: 名` 开头** → 那条空壳永远拿不到
`rows`/`cols`（它在 `## Sheet:` 行上被换掉、却留在列表里）→ `_build_data_summary` 读
`sh['rows']` 抛 KeyError → `build_excel_summary_prompt` 的 `except Exception` 兜底，
**100% 命中**。后果：「挑表头 + 首尾各 3 行样品」这条精心设计的摘要路径从未运行过，
prompt 里灌的是整张表的原文，而日志里一个字都没有。

⚠️ 既有测试 `test_prompt.py:41` 断言 `'Sheet1' in p` —— 它用的是**没有 `## Sheet:` 头**
的输入，恰好绕开了这条路径（有头时 Sheet1 才成为幻影）。本文件补的是**带头**的那一半。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.generation.prompt import (
    _parse_markdown_table,
    _build_data_summary,
    build_excel_summary_prompt,
)

# Excel 引擎 (`doc_excel.py:138`) 的真实输出形态：每张表以 `## Sheet: 名` 开头
SHEET_ONE = (
    "## Sheet: 销量\n\n"
    "| 月份 | 销量 |\n|---|---|\n"
    "| 1月 | 100 |\n| 2月 | 120 |\n| 3月 | 90 |\n"
)

FALLBACK_MARK = "数据内容（Markdown 表格）"


def test_sheet_header_takes_structured_path():
    """带 `## Sheet:` 头 → 走结构化摘要（不是原文回退）。这就是修复前 100% 走不通的那条。"""
    p = build_excel_summary_prompt(SHEET_ONE, "minimal")
    assert "共 3 行 × 2 列" in p, f"没走规模摘要路径：{p[:400]}"
    assert "字段: 月份, 销量" in p
    assert FALLBACK_MARK not in p, "仍回退成原文（except 兜底被执行）"


def test_no_phantom_sheet():
    """工作表列表里不得出现没有对应 `## Sheet:` 的幻影条目。"""
    info = _parse_markdown_table(SHEET_ONE)
    assert [s["name"] for s in info["sheets"]] == ["销量"], info["sheets"]
    assert info["total_sheets"] == 1, f"幻影工作表让总数多算: {info['total_sheets']}"


def test_multiple_sheets_keep_their_own_stats():
    """多张表各自的规模/字段必须分开统计（幻影条目会让第一张表的数据落到空壳上）。"""
    data = (
        "## Sheet: A\n\n| x |\n|---|\n| 1 |\n\n"
        "## Sheet: B\n\n| y |\n|---|\n| 2 |\n| 3 |\n"
    )
    info = _parse_markdown_table(data)
    got = [(s["name"], s.get("rows"), s.get("cols")) for s in info["sheets"]]
    assert got == [("A", 1, 1), ("B", 2, 1)], got
    out = _build_data_summary(data)
    assert "## A\n共 1 行 × 1 列" in out
    assert "## B\n共 2 行 × 1 列" in out


def test_headerless_table_still_gets_a_name():
    """无 `## Sheet:` 头的纯表格 → 兜底名（既有测试 `test_prompt.py:41` 依赖它）。"""
    info = _parse_markdown_table("| X | Y |\n|---|---|\n| 3 | 4 |")
    assert [s["name"] for s in info["sheets"]] == ["Sheet1"]
    assert info["sheets"][0]["rows"] == 1 and info["sheets"][0]["cols"] == 2


def test_empty_sheet_does_not_crash():
    """只有 `## Sheet: 名` 没有表格 = 合法输入（Excel 里真有空表）→ 输出 0 行 0 列。"""
    info = _parse_markdown_table("## Sheet: 空\n\n## Sheet: 有\n\n| a |\n|---|\n| 1 |\n")
    assert [s["name"] for s in info["sheets"]] == ["空", "有"]
    out = _build_data_summary("## Sheet: 空\n")
    assert "## 空" in out and "共 0 行 × 0 列" in out, out

    p = build_excel_summary_prompt("## Sheet: 空\n", "minimal")
    assert FALLBACK_MARK not in p, "空表不该触发原文回退"
