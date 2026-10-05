"""标题锚点 id —— 后端 HTML 导出侧（P1-15 的第三处同源）。

`markdown_render.add_heading_ids` 与前端 `lib/markdown-components.tsx` 是**跨语言的
同一份规则**：前端渲染出 `<h2 id="…">`，导出 HTML 也渲染出 `<h2 id="…">`，两边算不
一样就是「预览里能跳、导出里跳不动」（或反过来）。

⚠️ 本文件与 `frontend/src/test/headingIds.test.tsx` **必须用同一批样例**（同样的输入
    → 同样的 id）。加样例时两边一起加 —— 单侧加等于给自己发一张「已对齐」的假证书。

修复前这里有两个分叉：
  ① `inner` 是**已渲染的 HTML**，`**重点**` 到这一步是 `<strong>重点</strong>` ——
     不剥标签的话 id 里混进 `strong`；
  ② 时间戳剥离是一条宽松的 `\\d{2}:\\d{2}` 全删，「12:30 的会议」这种**非行尾时间**
     前端保留、后端删掉，同一个标题两边不同 id。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.services.markdown_render import markdown_to_html


def _id_of(md: str) -> str:
    """渲染一行标题，取出它的 id 属性。"""
    html = markdown_to_html(md)
    marker = 'id="'
    assert marker in html, f'没有生成 id：{html[:200]}'
    start = html.index(marker) + len(marker)
    return html[start:html.index('"', start)]


# ⚠️ 与前端 headingIds.test.tsx 逐个对应
_CASES = [
    # 输入 Markdown                   期望 id                说明
    ('## **重点** 内容',              '重点-内容'),          # 行内加粗（修复前 id 含 strong）
    ('## [文档](https://example.com/a) 阅读',  '文档-阅读'),  # 行内链接取文本
    ('## A & B',                      'a-b'),                # HTML 实体（修复前含 amp）
    ('## 开场 [▶ 12:30](#)',           '开场'),               # 视频转录时间戳（源码形态）
    ('## 12:30 的会议',               '1230-的会议'),         # 非行尾时间**保留**（对照点）
]


def test_heading_ids_match_frontend():
    """逐条与前端同批样例对齐。第 5 条是「宽松删时间」那个分叉的对照。"""
    for md, want in _CASES:
        assert _id_of(md) == want, f'{md!r} → {_id_of(md)!r}，期望 {want!r}'


def test_heading_ids_keeps_inline_markup_in_body():
    """剥的只是 **id**，正文里的 `<strong>` 必须原样保留（别把渲染也剥没了）。"""
    html = markdown_to_html('## **重点** 内容')
    assert '<strong>重点</strong>' in html
