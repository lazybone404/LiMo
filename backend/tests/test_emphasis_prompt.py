"""关键词加粗规范（`get_emphasis_format`）的注入范围与语义守卫。

加粗的语义是**强调**（2026-11-08 用户口径）：「加粗就是这一段的关键字，或者读者
需要着重看的东西」。它独立于折叠阅读器成立，故只该进 notes —— polished 是忠实
整理，要求它自己选重点与「不总结不提炼」直接冲突。

⚠️ 本文件守的是一次**已经发生过**的失效（2026-11-07）：同一位置曾写过一版只有禁令
没有用途的文案，三篇实机重生成后尼采那篇的加粗从 43 个塌到 1 个 —— 模型把强调全
搬进了引用块，而产物本身看起来完全正常。
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
    """**不按长度门控**（2026-11-08 当天改过一次就撤回）：短文里的加粗同样是阅读强调。"""
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


def test_定义它是什么而不规定它该有多少():
    """2026-11-08：数量与形状交给模型，规范只回答「加粗是什么」。

    这条同时守住两个方向 —— 只有禁令（2026-11-07 的塌陷）与堆数量形状（把加粗
    当成折叠标签的下游需求）。判据落在「定义句在场」且「不出现条数与长度限制」。
    """
    spec = get_emphasis_format()
    assert "强调" in spec
    assert "关键词" in spec and "需要着重看的内容" in spec
    for banned in ("1–3", "不超过", "字以内", "不要整句"):
        assert banned not in spec, f"数量/形状约束又回到了规范里：{banned}"
