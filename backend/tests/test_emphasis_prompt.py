"""关键词加粗规范（`get_emphasis_format`）的注入范围与措辞守卫。

消费方是折叠阅读器的「主题」视图：它把每节的加粗片段收成卡片标签
（`_fold-proto.html::boldWords()`）。故这份规范只该进 notes —— polished 是忠实整理，
要求它自己挑关键词与「不总结不提炼」直接冲突。

⚠️ 本文件守的是一次**已经发生过**的失效（2026-11-07）：同一位置曾写过一版只有禁令
没有用法与下限的文案，三篇实机重生成后尼采那篇的加粗从 43 个塌到 1 个 —— 模型把
强调全搬进了引用块，主题视图一张标签都没有，而产物本身看起来完全正常。
"""

from __future__ import annotations

import pytest

from app.generation.prompt import (
    build_excel_summary_prompt,
    build_notes_prompt,
    build_polish_prompt,
    get_emphasis_format,
)

_NOTES_TYPES = ("video", "audio", "webpage", "text", "pdf", "docx", "pptx", "image", "epub")


@pytest.mark.parametrize("source_type", _NOTES_TYPES)
def test_notes_各来源类型都注入(source_type):
    assert get_emphasis_format() in build_notes_prompt("正文内容", "标题", "plain", source_type)


@pytest.mark.parametrize("source_type", _NOTES_TYPES + ("xlsx",))
def test_整理版不注入(source_type):
    """polished 的加粗只能保留原文已有的。实测 151 份整理版里加粗中位数为 0 ——
    它本来就没有这层数据，要求它产出等于让它改写原文。"""
    assert get_emphasis_format() not in build_polish_prompt("正文内容", "标题", "plain", source_type)


def test_合成与_Excel_不注入():
    assert get_emphasis_format() not in build_notes_prompt(
        "## 来源文档\n材料正文", "标题", "plain", "composite")
    assert get_emphasis_format() not in build_excel_summary_prompt("| X |\n|---|", "plain")


def test_先讲用途再给数量最后才是禁令():
    """这条顺序是修那次塌陷的关键。

    判据落成「用途句出现在数量句之前，数量句出现在禁令句之前」——
    只断言三句都存在的话，把顺序改回去仍然会绿，而那正是塌陷的成因。
    """
    spec = get_emphasis_format()
    use = spec.find("阅读器会把每节里的加粗片段收成这一节的关键词标签")
    count = spec.find("1–3 个")
    ban = spec.find("不要加粗")
    assert use != -1 and count != -1 and ban != -1
    assert use < count < ban, "用途 / 数量 / 禁令的顺序被打乱了"


def test_有下限而不只有上限():
    """没有下限时模型会把「不要整句加粗」读成「不要加粗」。"""
    spec = get_emphasis_format()
    assert "1–3 个" in spec
    assert "每节" in spec


def test_把渲染侧的过滤前置了():
    """`boldWords()` 会丢弃超过 16 字的片段与伪代码保留字。生成侧不提前说，
    那些词就白写 —— 渲染侧是被动过滤，丢掉的词没人知道它本来该出现。"""
    spec = get_emphasis_format()
    assert "不是整句" in spec
    assert "for / if / return" in spec
    assert "然后 / 因此" in spec
