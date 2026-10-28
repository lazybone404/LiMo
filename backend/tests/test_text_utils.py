"""`split_segments_json` 的边界 —— 「什么算字幕 JSON」这条判据的唯一判定点。

它有两个消费方，形状完全相反：
- `generation/prompt._format_segments` 要**逐条带 `MM:SS - ` 前缀**（给模型看时间轴）
- `services/search_fuse._plain_body` 要**连续纯文本**（给 cross-encoder 判相关性）

同一条判据分岔到两处的失效形态是静默的：一处改了「认哪个键」，另一处照旧 ——
生成侧拿到一坨原文、或重排侧拿到 JSON 语法碎片，两边都不报错，指标只是「莫名为难」。
所以边界值必须钉在判据自己身上，而不是只钉在两个消费方上。
"""
import pytest

from app.core.text_utils import split_segments_json

SUB = ('{"segments": ['
       '{"start": 0.0, "text": "第一句"},'
       '{"start": 3.5, "text": "第二句"}'
       ']}')


def test_基本形态_返回分段与空余量():
    segs, rest = split_segments_json(SUB)
    assert [s["text"] for s in segs] == ["第一句", "第二句"]
    assert rest == ""


def test_尾随内容原样接回():
    """`search_index_text` 拼的是 `rawText + "\\n\\n" + 笔记`，整串 `json.loads`
    对真实库里的文档**必然失败**。展开后必须把笔记接回来，不能丢掉它。"""
    segs, rest = split_segments_json(SUB + "\n\n## 我的笔记\n\n一句话总结")
    assert len(segs) == 2
    assert rest.strip() == "## 我的笔记\n\n一句话总结"


def test_前导空白不影响判定():
    segs, _ = split_segments_json("\n\n  " + SUB)
    assert len(segs) == 2


@pytest.mark.parametrize("raw, why", [
    ("", "空串"),
    ("   ", "全空白"),
    ("## 普通正文\n\n没有 JSON", "不是 JSON"),
    ('{"title": "有对象但没 segments"}', "缺 segments 键"),
    ('{"segments": []}', "segments 是空列表 —— 展开后什么都没有，等于没展开"),
    ('{"segments": "不是列表"}', "segments 类型不对"),
    ('{"segments": [1, 2, 3]}', "列表里没有 dict"),
    ('["segments"]', "顶层是数组不是对象"),
    ('{坏掉的 JSON', "JSON 不合法"),
])
def test_不是字幕结构一律返回_None(raw, why):
    assert split_segments_json(raw) is None, why


def test_起点必须是真的开头():
    """正文中途出现的 JSON 不是「这篇文档的字幕结构」—— 认它会把一段引用的
    配置示例当成正文展开，反而制造出更难查的错位。"""
    assert split_segments_json("这是一段说明。\n" + SUB) is None
