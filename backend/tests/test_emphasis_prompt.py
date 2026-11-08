"""关键词加粗规范（`get_emphasis_format`）的注入范围与措辞守卫。

消费方是折叠阅读器的「主题」视图：它把每节的加粗片段收成卡片标签
（`lib/fold-scan.ts::boldWords()`）。故这份规范只该进 notes —— polished 是忠实整理，
要求它自己挑关键词与「不总结不提炼」直接冲突。
且**只有长文才进**（2026-11-08）：折叠入口本身只对长产物出现，短文没有标签可收，
还多背一条规范。阈值与分布见 `docs/TODO.md` 第 7 条。

⚠️ 本文件守的是一次**已经发生过**的失效（2026-11-07）：同一位置曾写过一版只有禁令
没有用法与下限的文案，三篇实机重生成后尼采那篇的加粗从 43 个塌到 1 个 —— 模型把
强调全搬进了引用块，主题视图一张标签都没有，而产物本身看起来完全正常。
"""

from __future__ import annotations

import json
import re

import pytest

from app.generation.prompt import (
    _LONG_BODY_MIN_CHARS,
    _format_segments,
    _source_body,
    build_excel_summary_prompt,
    build_notes_prompt,
    build_polish_prompt,
    get_emphasis_format,
)

_NOTES_TYPES = ("video", "audio", "webpage", "text", "pdf", "docx", "pptx", "image", "epub")

# 去标点后远超 `_LONG_BODY_MIN_CHARS` 的正文（19 字 × 200 ≈ 3600）
_LONG_BODY = "这是一段足够长的转录内容，用来越过加粗的长度门槛。" * 200


@pytest.mark.parametrize("source_type", _NOTES_TYPES)
def test_notes_长文注入(source_type):
    assert get_emphasis_format() in build_notes_prompt(_LONG_BODY, "标题", "plain", source_type)


@pytest.mark.parametrize("source_type", _NOTES_TYPES)
def test_notes_短文不注入(source_type):
    """短产物不显示折叠入口，主题视图没有标签可收 —— 规范对它是多余的。"""
    assert get_emphasis_format() not in build_notes_prompt("正文内容", "标题", "plain", source_type)


def test_长度判据不把时间戳算进去():
    """守的是 `_source_body` 的存在理由。

    `_format_segments()` 给每段加 `MM:SS - ` 前缀，时间戳里的数字属于 `\\w`，
    `re.sub(r"[^\\w]", "", ...)` 剔不掉。直接量它的返回值，一小时视频会凭空涨
    两三千字符、静默越过阈值 —— 下面的对照断言证明这两个量法确实分叉。
    """
    segs = {"segments": [{"start": i * 10, "text": "这是一段字幕内容测试文字"}
                         for i in range(240)], "language": "zh"}
    raw = json.dumps(segs, ensure_ascii=False)

    body_len = len(re.sub(r"[^\w]", "", _source_body(raw)))
    formatted_len = len(re.sub(r"[^\w]", "", _format_segments(raw)))
    assert body_len < _LONG_BODY_MIN_CHARS <= formatted_len, (
        f"这组样本本应落在「正文短、带时间戳后过线」的区间：{body_len} / {formatted_len}")
    assert get_emphasis_format() not in build_notes_prompt(raw, "标题", "plain", "video")


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
