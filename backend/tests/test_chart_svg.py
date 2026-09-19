"""语义图表 SVG 生成器回归测试 — chart_svg.py

核心不变式（2026-09-19）：
  生成的 SVG **必须**落在 PIL 自绘渲染器能解析的子集内。这件事不能靠测试发现——
  `<marker>` / `class=` / `url(#…)` / `<defs>` **不是被校验拦住的，而是被静默忽略的**
  （`_sanitize_svg` 只删 script/on*/href 四类），用了它们照样校验通过、照样落盘，
  只是箭头/样式无声消失。所以防线在 `render_spec_svg()` 出口的 `_FORBIDDEN_RE` 自检，
  本文件对它的断言只做**二次回归**（验证「若有人绕过自检，这些断言仍会响」）。

覆盖：
1. 产物通过现有 `_validate_svg` 全链（良构 / 尺寸 / 形状 / 有文字）
2. 根 width/height 与 viewBox 双写一致且 ≤640（`_parse_attr_size` 取第一个 width）
3. 箭头是 `<polygon>` 且**真实渲染出来**（不 mock —— 这是「静默漏画」的唯一探测手段）
4. 对照组：关掉 arrow 后同一区域为空（证明上面的检测不是恒真）
5. XML 转义（label 含 & < >）
6. 绘制顺序：节点在边标签遮罩之后（后绘的压住先绘的）
7. `_FORBIDDEN_RE` 二次回归 + 出口自检真的在渲染路径上（mock 注入禁元素 → raise）

真实渲染不 mock —— PIL 是硬依赖，测试环境可用。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import copy
import re
from io import BytesIO
from unittest import mock

from PIL import Image

from app.services import chart_layout as L
from app.services import chart_svg
from app.services.chart_svg import render_spec_svg, RenderSpecError
from app.services.chart_renderer import _validate_svg, _render_png, _parse_attr_size

_spec = L.expand({"layout": "hchain", "title": "流程",
                  "nodes": [{"label": "输入"}, {"label": "清洗", "focus": True},
                            {"label": "输出"}]})
svg = render_spec_svg(_spec)

# ── 1. 过现有校验链 ──

ok, reason = _validate_svg(svg)
assert ok, f'生成的 SVG 未通过 _validate_svg: {reason}'
print("PASS: 生成 SVG 通过 _validate_svg 全链（良构/尺寸/形状/有文字）")

# ── 2. 画布尺寸双写一致 ──

vb = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg)
assert vb, 'viewBox 缺失'
attr = _parse_attr_size(svg)
assert attr is not None, 'width/height 属性缺失（_parse_attr_size 取不到画布）'
assert attr[0] == int(vb.group(1)) == _spec['width'], f'width 不一致 {attr} vs {vb.groups()}'
assert attr[1] == int(vb.group(2)) == _spec['height'], f'height 不一致 {attr} vs {vb.groups()}'
assert attr[0] <= 640 and attr[1] <= 640, f'画布超限 {attr}'
assert len(svg) < 64 * 1024, f'SVG 过长 {len(svg)}'
print(f"PASS: 画布 {attr[0]}x{attr[1]} — width/height/viewBox 三者一致且 ≤640")

# ── 3. 箭头是 polygon 且真实渲染 ──

assert '<polygon' in svg, '边应画出手绘箭头 polygon'
assert '<marker' not in svg and 'url(#' not in svg, '不得使用 PIL 子集外的箭头机制'

data = _render_png(svg)
assert data and data.startswith(b'\x89PNG'), f'真实渲染应返回 PNG，实际 {type(data)}'
im = Image.open(BytesIO(data)).convert('RGB')
px = im.load()
ex, ey = _spec['edges'][0]['end']       # 第一条边指向第 2 个节点的左边缘中点
# 2x 缩放。窗口必须同时避开两样东西，只圈住箭头三角形本体的上半：
#   ① 节点描边（正好在 x=ex 处）        → x 上界退到 ex-2
#   ② 水平连线（正好在 y=ey 处）        → y 上界退到 ey-2
x0, x1 = int((ex - 9) * 2), int((ex - 2) * 2)
y0, y1 = int((ey - 6) * 2), int((ey - 2) * 2)
arrow_dark = sum(1 for xx in range(x0, x1) for yy in range(y0, y1)
                 if sum(px[xx, yy]) < 500)
assert arrow_dark > 15, f'箭头应渲染出深色像素，实际 {arrow_dark}（静默漏画？）'
print(f"PASS: 箭头 polygon 真实渲染 — 终点邻域 {arrow_dark}px 深色")

# ── 4. 对照组：无箭头时同区域为空（证明上面的检测不是恒真）──

spec_noarrow = copy.deepcopy(_spec)
for _e in spec_noarrow['edges']:        # 3 节点 = 2 条边，全关
    _e['arrow'] = False
svg_na = render_spec_svg(spec_noarrow)
assert '<polygon' not in svg_na, 'arrow=False 时不应有 polygon'
data_na = _render_png(svg_na)
px_na = Image.open(BytesIO(data_na)).convert('RGB').load()
na_dark = sum(1 for xx in range(x0, x1) for yy in range(y0, y1)
              if sum(px_na[xx, yy]) < 500)
assert na_dark == 0, f'无箭头时该区域应无深色像素，实际 {na_dark} —— 检测方法失效'
print("PASS: 对照组 — 关闭箭头后同区域 0 深色像素（检测方法有效）")

# ── 5. XML 转义 ──

spec_esc = L.expand({"layout": "hchain",
                     "nodes": [{"label": "A&B"}, {"label": "<C>"}]})
svg_esc = render_spec_svg(spec_esc)
assert 'A&amp;B' in svg_esc, svg_esc
assert '&lt;C&gt;' in svg_esc, svg_esc
assert '<C>' not in svg_esc, '未转义的尖括号会破坏 XML 良构'
ok, reason = _validate_svg(svg_esc)
assert ok, f'含特殊字符的 label 应仍良构: {reason}'
print("PASS: XML 转义 — label 含 & < > 仍良构（&amp; / &lt; / &gt;）")

# ── 6. 绘制顺序：节点在边标签遮罩之后 ──

spec_grid = L.expand({
    "layout": "grid",
    "rows": [[{"label": "前端"}, {"label": "缓存"}], [{"label": "接口层"}]],
    "edges": [{"from": [0, 0], "to": [1, 0], "label": "调用"}]})
assert spec_grid['labels'], 'grid 带 label 的边应产生遮罩'
svg_grid = render_spec_svg(spec_grid)
i_mask = svg_grid.index('rx="3"')      # 标签遮罩：小圆角 + 底色
i_node = svg_grid.index('rx="6"')      # 节点卡片
i_text = svg_grid.rindex('font-size="11"')
assert i_mask < i_node, '遮罩应在节点之前绘制（后绘的节点压住遮罩）'
assert i_text < i_node, '标签文字也应在节点之前绘制'
print("PASS: 绘制顺序 — 背景 → 连线 → 标签遮罩 → 箭头 → 节点")

# ── 7. _FORBIDDEN_RE 二次回归 + 出口自检真的在渲染路径上 ──

for bad in ('<marker id="m"/>', 'class="node"', 'url(#grad)', '<defs>',
            '<image href="x"/>', '<use href="#a"/>', 'style="fill:red"'):
    assert chart_svg._FORBIDDEN_RE.search(bad), f'禁元素未被识别: {bad}'
print("PASS: _FORBIDDEN_RE 覆盖 marker / class / url(# / defs / image / use / style")

# 出口自检必须真的拦得住（而非只在测试里断言正则）
with mock.patch.object(chart_svg, '_node_svg',
                       return_value='<defs><marker id="m"/></defs>'):
    try:
        chart_svg.render_spec_svg(_spec)
        raise AssertionError('出口自检未生效 —— 禁元素混进了产物')
    except RenderSpecError as e:
        assert 'PIL 子集外' in str(e), e
print("PASS: render_spec_svg 出口自检拦下子集外元素（RenderSpecError）")

# 合法布局不误报
assert render_spec_svg(_spec) and render_spec_svg(spec_grid)
print("PASS: 合法布局不被出口自检误伤")

# ── 8. 四种 layout 都能生成可渲染的 SVG ──

for name, sp in (
    ("hchain", {"layout": "hchain", "nodes": [{"label": "A"}, {"label": "B"}]}),
    ("layers", {"layout": "layers", "layers": [
        {"name": "层一", "nodes": [{"label": "甲"}, {"label": "乙"}]},
        {"name": "层二", "nodes": [{"label": "丙"}]}]}),
    ("timeline", {"layout": "timeline",
                  "nodes": [{"label": "开始", "sub": "00:00"},
                            {"label": "高潮", "sub": "05:30"}]}),
    ("grid", {"layout": "grid", "rows": [[{"label": "X"}], [{"label": "Y"}]],
              "edges": [{"from": [0, 0], "to": [1, 0]}]}),
):
    s = render_spec_svg(L.expand(sp))
    okk, rr = _validate_svg(s)
    assert okk, f'{name}: 未通过校验 {rr}'
    d = _render_png(s)
    assert d and d.startswith(b'\x89PNG'), f'{name}: 真实渲染失败'
print("PASS: 四种 layout 均生成可校验、可真实渲染的 SVG")

# ── 等比缩放：内容包在 <g transform="scale(k)">，背景必须留在组外 ──
spec_big = L.expand({"layout": "timeline",
                     "nodes": [{"label": f"事件{i}", "sub": "00:00"} for i in range(1, 13)]})
if spec_big['scale'] < 1.0:
    s = render_spec_svg(spec_big)
    sw = round(spec_big['width'] * spec_big['scale'])
    sh = round(spec_big['height'] * spec_big['scale'])
    assert f'viewBox="0 0 {sw} {sh}"' in s, '画布尺寸应为缩放后的尺寸'
    assert f'width="{sw}" height="{sh}"' in s, 'width/height 与 viewBox 必须双写一致'
    assert sw <= 640 and sh <= 640, f'缩放后仍须 ≤640，实际 {sw}x{sh}'
    assert f'<g transform="scale({spec_big["scale"]:.6f})">' in s, '内容应包在 scale 组内'
    # 背景若进了组内，100% 会被缩成 100%×k → 四边留白带
    assert s.index('<rect width="100%') < s.index('<g transform='), \
        '背景必须在 scale 组之外（否则填不满画布）'
    okk, rr = _validate_svg(s)
    assert okk, f'缩放后的 SVG 未通过校验 {rr}'
    assert _render_png(s), '缩放后的 SVG 真实渲染失败'
    print(f"PASS: 等比缩放产物 — 画布 {sw}x{sh} / 背景在组外 / 真实渲染通过")

    # k≠1 时 scale 组内的文字也要跟着缩 → 像素总量应明显少于未缩放的同图
    spec_flat = L.expand({"layout": "timeline",
                          "nodes": [{"label": f"事件{i}", "sub": "00:00"} for i in range(1, 13)]},
                         height_cap=100000)
    assert spec_flat['scale'] == 1.0
    d_big = _render_png(s)
    d_flat = _render_png(render_spec_svg(spec_flat))
    assert len(d_big) < len(d_flat), '缩放后的 PNG 体积应小于未缩放版本'
    print("PASS: 缩放对整图（含文字）生效 — 产物体积随缩放下降")
else:
    print("SKIP: 等比缩放用例未触发（该 timeline 未超限）")

# ── 装饰层（shapes）与节点形状的渲染 ──
# 这两段**手工构造布局产物**而不走 expand：既有 4 个算子按设计不产出 shape
# （形状层只服务新算子），走算子就测不到。布局侧对 shapes 的 bbox/越界校验
# 另由 test_chart_layout 覆盖，这里只管「画得出来、画得对」。
_bare = L.expand({"layout": "hchain", "nodes": [{"label": "甲"}, {"label": "乙"}]})

_sp = dict(_bare)
_sp["shapes"] = [
    {"kind": "line", "x1": 16, "y1": 8, "x2": 300, "y2": 8, "stroke": "muted"},
    {"kind": "rect", "x": 16, "y": 20, "w": 200, "h": 100, "stroke": "accent",
     "fill": "none", "dashed": True},
]
_s = render_spec_svg(_sp)
assert '<line x1="16" y1="8" x2="300" y2="8"' in _s, 'shapes 的 line 未渲染'
assert '<rect x="16" y="20" width="200" height="100"' in _s, 'shapes 的 rect 未渲染'
assert 'stroke="#eb6c36"' in _s and 'stroke-dasharray' in _s, 'shapes 的颜色/虚线未透传'
# 绘制顺序：装饰层必须在连线**之前**（否则轴线/分组框会盖住连线与节点）
assert _s.index('<line x1="16"') < _s.index('<path d='), '装饰层必须画在连线之前'
_ok, _why = _validate_svg(_s)
assert _ok, f'含 shapes 的产物未通过校验 {_why}'
assert _render_png(_s), '含 shapes 的产物真实渲染失败'
print("PASS: shapes 通道渲染为 line/rect、画在连线之前，真实渲染通过")

# 颜色只认角色名：未知值退回 muted，裸 hex 绝不透传（固定 skin 单点维护）
_sp2 = dict(_bare)
_sp2["shapes"] = [{"kind": "line", "x1": 16, "y1": 8, "x2": 100, "y2": 8,
                   "stroke": "#ff00ff"}]
assert '#ff00ff' not in render_spec_svg(_sp2), '裸 hex 不应透传到产物'
print("PASS: shapes 的颜色只认角色名，未知值退回 muted（skin 不被绕开）")

# 形状分发：diamond → polygon / stadium → rx=h/2 / 空 label 的 circle → 纯轮廓
_sp3 = {
    "width": 400, "height": 120, "scale": 1.0, "title": "", "title_h": 0,
    "labels": [], "edges": [],
    "shapes": [{"kind": "line", "x1": 16, "y1": 110, "x2": 384, "y2": 110}],
    "nodes": [
        {"x": 20, "y": 20, "w": 120, "h": 64, "label": "菱形", "sub": None,
         "focus": False, "shape": "diamond"},
        {"x": 200, "y": 20, "w": 160, "h": 44, "label": "体育场", "sub": None,
         "focus": False, "shape": "stadium"},
        {"x": 20, "y": 92, "w": 20, "h": 20, "label": "", "sub": None,
         "focus": False, "shape": "circle"},
    ],
}
_s3 = render_spec_svg(_sp3)
assert '<polygon points=' in _s3, 'diamond 应画成 polygon（菱形轮廓）'
assert 'rx="22"' in _s3, 'stadium 应用 rx=h/2 的圆角矩形'
assert '<circle cx="30" cy="102" r="10"' in _s3, 'circle 节点未按外接矩形内切'
assert '<text' in _s3 and _s3.count('<text') == 2, \
    f'空 label 的 circle 不应产出 text（实际 {_s3.count("<text")} 个）'
_ok3, _why3 = _validate_svg(_s3)
assert _ok3, f'形状节点产物未通过校验 {_why3}'
assert _render_png(_s3), '形状节点产物真实渲染失败'
print("PASS: diamond → polygon / stadium → 圆角矩形 / circle → 纯轮廓（无空 text）")

print('Chart svg tests done')
