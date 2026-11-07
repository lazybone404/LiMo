"""结构规范（`get_fold_structure_format`）的注入范围与内容守卫。

它的消费方是折叠阅读器 —— 读的完全是 Markdown 原生元素：标题给层级、加粗给关键词、
列表给要点、引用块给原文。故这份规范**必须两侧都注入**（notes 与 polished），
与 `get_markdown_block_format` 只进 notes 不同。

⚠️ 本文件守的是「漏注入」这一类静默失效：少注入一处不报错、产物照样生成，
只是那一侧的产物折叠出来是散的。三篇不同领域的真实产物实测（2026-11-07）里
出现过「父节中文序号 + 子节阿拉伯数字」混写导致父子被并排 —— 正是第 2 条要挡的写法。
"""

from __future__ import annotations

import pytest

from app.generation.prompt import (
    build_excel_summary_prompt,
    build_notes_prompt,
    build_polish_prompt,
    get_fold_structure_format,
)

# notes 侧走 `build_notes_prompt` 的分支；xlsx / composite 有独立骨架，见下面的排除用例
_NOTES_TYPES = ("video", "audio", "webpage", "text", "pdf", "docx", "pptx", "image", "epub")
# polished 侧 —— xlsx 也走这里（notes 侧它走数据摘要，polished 侧没有分支），故一并列出
_POLISH_TYPES = _NOTES_TYPES + ("xlsx",)


@pytest.mark.parametrize("source_type", _NOTES_TYPES)
def test_notes_各来源类型都注入结构规范(source_type):
    p = build_notes_prompt("正文内容", "标题", "plain", source_type)
    assert get_fold_structure_format() in p


@pytest.mark.parametrize("source_type", _POLISH_TYPES)
def test_整理版各来源类型都注入结构规范(source_type):
    """polished 是折叠的主场景 —— 实测三篇产物里它几乎全是纯段落，
    没有结构规范时连标题层级都由模型自由发挥，折叠出来的节数是随机的。"""
    p = build_polish_prompt("正文内容", "标题", "plain", source_type)
    assert get_fold_structure_format() in p


def test_两侧注入的是同一份文案():
    """同源判据：两份产物对「标题怎么分级」必须给出一致要求。

    失效形态是有人给 polished 抄一份改过的文案（「整理版不要太结构化」之类），
    于是同一篇文档的 notes 与 polished 折叠出来的层级规则不同，而两边都正常生成。
    判据落成「两侧 prompt 里出现的是同一个返回值」，不是「两侧都有类似的话」。
    """
    spec = get_fold_structure_format()
    assert spec in build_notes_prompt("正文", "标题", "plain", "video")
    assert spec in build_polish_prompt("正文", "标题", "plain", "video")


def test_合成与_Excel_不注入():
    """对照：两者都走独立骨架（合成有自己的 `evidence` 规范、Excel 走数据摘要）。
    结构规范若被拼进去，合成产物会同时收到两套标题层级要求。"""
    composite = build_notes_prompt("## 来源文档\n材料正文", "标题", "plain", "composite")
    assert get_fold_structure_format() not in composite

    excel = build_excel_summary_prompt("| X |\n|---|\n| 1 |", "plain")
    assert get_fold_structure_format() not in excel


def test_规范写了五条可检查的判据():
    """防「被删成空壳」：每一条都要落到一个可观察的写法上。

    ⚠️ 这里断言的是**判据本身**（标题三级 / 编号一套 / 首段一句 / 加粗粒度 /
    围栏是整体块），不是措辞。措辞可以改，判据少一条折叠就少一种可用信号。
    """
    spec = get_fold_structure_format()
    # 1 标题层级
    assert "`##`" in spec and "`###`" in spec and "`####`" in spec
    assert "不许跳级" in spec
    # 2 编号体系
    assert "`1.1`" in spec and "一、" in spec
    assert "不能混用" in spec
    # 3 每节首段
    assert "第一段是一句话概括" in spec
    # 4 加粗与引用块的分工
    assert "加粗只标术语、判据、数字" in spec
    assert "引用块 `>` 只放原文里的话" in spec
    # 5 原子块
    assert "表格与代码围栏是整体块" in spec
    assert "围栏只用于代码、伪代码、原样引用的文本" in spec


def test_规范不写外观():
    """与 `get_markdown_block_format` 同一条纪律：模型管结构，CSS 管长相。"""
    spec = get_fold_structure_format()
    for word in ("颜色", "色值", "边框", "圆角", "阴影", "背景色"):
        assert word not in spec, f"结构规范里出现了外观词：{word}"


def test_规范不含不可判定的说法():
    """「尽量」「适当」「尽可能」这类词没有判据 —— 模型照不照做无法观察，
    也无法写测试。六条规则各自必须落到一个具体的写法上。"""
    spec = get_fold_structure_format()
    for word in ("尽量", "适当", "尽可能", "酌情"):
        assert word not in spec, f"结构规范里出现了不可判定的说法：{word}"
