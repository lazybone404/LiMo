"""候选窗口选择 —— 送进重排的那 480 字，是不是**有答案**的那 480 字。

这个文件存在的唯一理由是一条**会静默毁掉实验**的缺陷：`_make_snippet` 用
`re.split(r"\\s+", query)` 定位，中文口语查询没有空格 ⇒ 整个查询是一个 term
⇒ `body.find()` 必然失败 ⇒ 回退 `body[:width * 2]`，也就是**恒等于正文开头**。
它不报错、不抛异常，只是让长文档里真正的答案永远进不了重排的视野 —— 最坏
情况是 `_experiments/07-rerank/` 得出「rerank 没用」的假阴性。

故本文件的第一条测试**自带对照**（同一个 body、同一个 query，`_make_snippet`
看不到答案而 `rerank_window` 看得到）。少了这个对照，把选择逻辑退回「取开头」
也能让其它几条通过。
"""
from app.core.search import _make_snippet, rerank_window

#: 答案埋在 3000 字之后 —— 短文档没这个问题，长的才有
ANSWER = "机器学习中的注意力机制是这样工作的：它把查询与键做点积，除以根号维度，再经 softmax 得到权重。"
BODY = "背景材料" * 750 + ANSWER + "后续讨论" * 500


def test_中文无空格查询命中正文中段_对照组看不到():
    """⚠️ 本文件的核心断言，两半缺一不可。

    上半句证明选择器有效；下半句证明**这条测试有区分力** —— 没有它，把实现
    换成 `body[:width]` 也能过上面那句（只要答案恰好在前 480 字里，而这里它不在）。
    """
    window = rerank_window(BODY, "注意力机制是怎么工作的")
    assert ANSWER[:40] in window, "答案必须落在送进重排的窗口里"
    assert len(window) <= 480

    # 负向对照：同一份输入走 `_make_snippet` 的定位逻辑，答案**不在**里面
    snippet = _make_snippet(BODY, "注意力机制是怎么工作的", width=480)
    assert ANSWER[:40] not in snippet
    assert snippet.startswith("背景材料"), "它退化成了正文开头"


def test_英文带空格查询同样有效():
    body = "intro " * 800 + "attention is all you need, the transformer paper. " + "outro " * 500
    window = rerank_window(body, "attention transformer paper")
    assert "all you need" in window


def test_窗口居中于命中区而不是从最左命中起算():
    """命中区居中 ⇒ 左右两边的截断风险对称。

    只留 1/4 在命中之前（`start = left_hit - width // 4`）时，命中区横跨 300 字
    的答案会在右边缘被切掉，而那是最常见的长答案形态。
    """
    answer = "关键词甲" + "中" * 400 + "关键词乙"
    body = "前" * 2000 + answer + "后" * 2000
    window = rerank_window(body, "关键词甲 关键词乙", width=480)
    # 两个词都在窗口里 ⇒ 命中区（跨度 400+）被完整覆盖，没有偏向任何一边
    assert "关键词甲" in window and "关键词乙" in window


def test_短正文原样返回不截断():
    assert rerank_window("很短的一段话", "什么词") == "很短的一段话"


def test_空正文返回空串():
    assert rerank_window("", "q") == ""
    assert rerank_window("   ", "q") == ""


def test_无任何命中时退回开头():
    """与 `_make_snippet` 的退化方向一致 —— 至少是正文，不是空。"""
    body = "甲" * 1000
    assert rerank_window(body, "完全对不上的词组") == body[:480]


def test_宽度为0不炸():
    assert rerank_window("abc" * 1000, "abc", width=0) == ""


def test_查询为空时退回开头():
    body = "乙" * 1000
    assert rerank_window(body, "") == body[:480]


def test_单字查询不命中():
    """`_tokenize` 对中文输出二字组 ⇒ 单个汉字不产生任何 token。

    这不是缺陷：单字查询本来也没有区分度（「的」「是」全库都有），而真正
    需要它命中的场景是「用户敲了一个字就想搜」——那走的是 `search()` 的
    另一条路（那里 `_tokenize` 同样空，FTS 返回空结果，行为一致）。
    """
    body = "机" + "器" * 2000
    assert rerank_window(body, "机") == body[:480]
