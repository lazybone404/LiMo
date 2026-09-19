"""图表渲染器回归测试 — 尺寸校验绕过（P0-2）+ 图表只入 notes（P0-3）+ 协议围栏收紧

对齐 pi-docs/图表围栏内部协议设计说明.md（2026-08-16 定稿）：
- 只认 ```chart 专用围栏；svg/xml 围栏、裸内联 <svg> 一律不碰（用户内容零触碰）
- 围栏必然被替换：成功 → ![图表](assets/chart_N.png)；失败 → 「📊 图表生成失败」文本
- 产物零残留：围栏/源码不出现在最终内容中

覆盖：
1. P0-2 `width="50000"` + 合法 viewBox → 校验失败（resvg 实际画布由属性决定）
2. 正常尺寸 / 仅 viewBox → 校验通过
3. `_post_process(render_chart=False)`（polished 侧）→ 不渲染、不写 chart 文件
4. chart 围栏 → 渲染出 chart_N.png 替换引用
5. ```svg 围栏 / 裸内联 <svg> → 原样保留不处理（协议收紧回归）
6. 围栏校验失败 → 替换为失败文本（零残留，无围栏/源码/图片引用）
7. chart 围栏内非 SVG → 原样保留不处理
8. svg 字面量垃圾清理 + 跨行 svg 单词不误清理

resvg 渲染用 mock 兜底，不依赖打包环境是否携带 resvg_py。
"""
import sys, os, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest import mock

from app.services.chart_renderer import render_charts, _validate_svg

PNG = b'\x89PNG\r\n\x1a\n' + b'fake-png-bytes'

# ── P0-2：width/height 属性绕过校验 ──

# 合法 viewBox + 超大 width 属性 → 校验必须失败（resvg 按属性输出画布，防 40GB OOM）
# 带 text：隔离「画布超限」维度（2026-08-16 起校验链要求图必须有文字）
big_svg = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100" width="50000" height="50000">
  <rect x="10" y="10" width="80" height="50" fill="#4A90D9"/><text x="50" y="40" font-size="14">节点</text>
</svg>'''
ok, reason = _validate_svg(big_svg)
assert not ok, f'超大 width 应校验失败，实际通过: {reason}'
assert '画布超限' in reason, reason
print('PASS: P0-2 超大 width 属性被拦截')

# 正常尺寸（width + viewBox 均 ≤640）→ 通过
good_svg = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100" width="400" height="300">
  <rect x="10" y="10" width="80" height="50" fill="#4A90D9"/><text x="50" y="40" font-size="14" fill="#1f2937">节点 A</text>
</svg>'''
ok, reason = _validate_svg(good_svg)
assert ok, f'正常尺寸应通过校验: {reason}'
print('PASS: P0-2 正常尺寸校验通过')

# 仅 viewBox、无 width/height（属性缺失 → 回退 viewBox 校验）→ 通过
vb_only_svg = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100">
  <rect x="10" y="10" width="80" height="50" fill="#4A90D9"/><text x="50" y="40" font-size="14">节点 A</text>
</svg>'''
ok, reason = _validate_svg(vb_only_svg)
assert ok, f'仅 viewBox 应回退校验并通过: {reason}'
print('PASS: P0-2 仅 viewBox 回退校验')

# ── 2026-08-16：图表必须有文字（用户反馈「图表里没有文字」）──

# 有形状但零 <text> → 校验失败（LLM 只画框不写字的图对读者无意义）
no_text_svg = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100" width="200" height="100">
  <rect x="10" y="10" width="80" height="50" fill="#4A90D9"/><line x1="95" y1="35" x2="110" y2="35" stroke="#999"/>
</svg>'''
ok, reason = _validate_svg(no_text_svg)
assert not ok, f'零文字图应校验失败，实际通过: {reason}'
assert '无文字' in reason, reason
print('PASS: 零文字图校验失败（无文字说明）')

# 空 <text></text> / 自闭合 <text/> → 有标签但零文字内容，同样失败
empty_text_svg = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100" width="200" height="100">
  <rect x="10" y="10" width="80" height="50" fill="#4A90D9"/><text x="50" y="40" font-size="14"></text>
</svg>'''
ok, reason = _validate_svg(empty_text_svg)
assert not ok, f'空 text 标签应校验失败，实际通过: {reason}'
assert '无文字' in reason, reason

self_closed_text_svg = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100" width="200" height="100">
  <rect x="10" y="10" width="80" height="50" fill="#4A90D9"/><text x="50" y="40" font-size="14"/>
</svg>'''
ok, reason = _validate_svg(self_closed_text_svg)
assert not ok, f'自闭合 text 应校验失败，实际通过: {reason}'
assert '无文字' in reason, reason
print('PASS: 空 text / 自闭合 text 校验失败（无文字说明）')

with tempfile.TemporaryDirectory() as tmp:
    with mock.patch('app.services.chart_renderer._render_png', return_value=PNG):
        out = render_charts('```chart\n' + no_text_svg + '\n```', tmp)
    assert '📊 图表生成失败' in out, f'零文字图应替换为失败文本: {out}'
    assert '无文字说明' in out, f'失败文本应说明原因: {out}'
    assert '![图表](assets/' not in out, f'零文字图不应产生图片引用: {out}'
    assert not os.path.exists(os.path.join(tmp, 'assets', 'chart_1.png')), '零文字图不应写 chart 文件'
print('PASS: 零文字图围栏替换为失败文本（无文字说明）')

# ── chart 围栏正常渲染（mock resvg，不依赖打包环境）──

def _chart_md():
    return '```chart\n' + good_svg + '\n```'

with tempfile.TemporaryDirectory() as tmp:
    with mock.patch('app.services.chart_renderer._render_png', return_value=PNG):
        out = render_charts(_chart_md(), tmp)
    assert '![图表](assets/chart_1.png)' in out, out
    assert '```chart' not in out, f'围栏应被替换消失（零残留）: {out}'
    assert os.path.exists(os.path.join(tmp, 'assets', 'chart_1.png'))
    # 2026-08-16：渲染成功时同步保存 SVG 源码片段（调试 + 导出矢量图）
    assert os.path.exists(os.path.join(tmp, 'assets', 'chart_1.svg')), '应保存 chart_1.svg 源码片段'
    with open(os.path.join(tmp, 'assets', 'chart_1.svg'), encoding='utf-8') as f:
        assert '<svg' in f.read(), 'SVG 片段应为 LLM 原始输出'
print('PASS: chart 围栏渲染 PNG 并替换引用（同步保存 SVG 片段）')

# ── P0-3：图表只入 notes —— _post_process 的 render_chart 门控 ──
# 双产物若各调一次 render_charts，counter 每次重置为 0 → 写同名 chart_1.png 互踩。
# 修复：polished 传 render_chart=False 不渲染，只有 notes 渲染。
from app.routers.generate import _post_process

with tempfile.TemporaryDirectory() as tmp:
    # polished 侧：render_chart=False → 不渲染（保留原 chart 围栏，不写 chart 文件）
    out = _post_process(_chart_md(), ['chart'], None, 'text', tmp, render_chart=False)
    assert '```chart' in out, 'polished 侧应保留原 chart 围栏'
    assert not os.path.exists(os.path.join(tmp, 'assets', 'chart_1.png')), 'polished 侧不应生成 chart 文件'

    # notes 侧：render_chart=True（默认）→ 渲染
    with mock.patch('app.services.chart_renderer._render_png', return_value=PNG):
        out2 = _post_process(_chart_md(), ['chart'], None, 'text', tmp, render_chart=True)
    assert '![图表](assets/chart_1.png)' in out2, out2
    assert os.path.exists(os.path.join(tmp, 'assets', 'chart_1.png'))
print('PASS: P0-3 双产物 render_chart 门控（polished 不渲染 / notes 渲染）')

# ── 协议收紧：svg/xml 围栏、裸内联 <svg> 一律不碰（用户内容零触碰）──

svg_fence_md = '```svg\n' + good_svg + '\n```'
with tempfile.TemporaryDirectory() as tmp:
    with mock.patch('app.services.chart_renderer._render_png', return_value=PNG):
        out = render_charts(svg_fence_md, tmp)
    assert '```svg\n' in out, f'用户 svg 围栏应原样保留: {out}'
    assert '<svg' in out, f'用户 svg 围栏内容应保留: {out}'
    assert '![图表](assets/' not in out, f'svg 围栏不应被处理: {out}'
    assert not os.path.exists(os.path.join(tmp, 'assets', 'chart_1.png')), 'svg 围栏不应写 chart 文件'
print('PASS: 用户 ```svg 围栏不被处理')

inline_svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100"><rect x="10" y="10" width="80" height="50" fill="#4A90D9"/></svg>'
with tempfile.TemporaryDirectory() as tmp:
    with mock.patch('app.services.chart_renderer._render_png', return_value=PNG):
        out = render_charts(inline_svg, tmp)
    assert '<svg' in out, f'裸内联 <svg> 应原样保留: {out}'
    assert '![图表](assets/' not in out, f'裸内联不应被渲染: {out}'
    assert not os.path.exists(os.path.join(tmp, 'assets', 'chart_1.png')), '裸内联不应写 chart 文件'
print('PASS: 裸内联 <svg> 不被处理')

# 混合：chart 围栏正常渲染，svg 围栏 / 裸内联原样保留
mixed_md = '```chart\n' + good_svg + '\n```\n```svg\n' + vb_only_svg + '\n```\n' + inline_svg
with tempfile.TemporaryDirectory() as tmp:
    with mock.patch('app.services.chart_renderer._render_png', return_value=PNG):
        out = render_charts(mixed_md, tmp)
    assert '![图表](assets/chart_1.png)' in out, out
    assert '```svg' in out, f'svg 围栏应原样保留: {out}'
    assert out.count('<svg') == 2, f'裸内联 + svg 围栏内 svg 应保留（共 2 处 <svg）: {out}'
print('PASS: 混合场景只处理 chart 围栏，用户 svg 内容不受影响')

# ── 围栏校验失败 → 替换为失败文本（零残留）──
fenced_bad_md = '```chart\n' + big_svg + '\n```'
with tempfile.TemporaryDirectory() as tmp:
    with mock.patch('app.services.chart_renderer._render_png', return_value=PNG):
        out = render_charts(fenced_bad_md, tmp)
    assert '```chart' not in out, f'失败围栏应被替换消失（零残留）: {out}'
    assert '<svg' not in out, f'失败源码应消失: {out}'
    assert '📊 图表生成失败' in out, f'应替换为失败提示文本: {out}'
    assert out.count('图表生成失败') == 1, f'失败标注应只出现一次: {out}'
    assert '![图表](assets/' not in out, f'失败不应产生图片引用: {out}'
    assert not os.path.exists(os.path.join(tmp, 'assets', 'chart_1.png')), '失败不应写 chart 文件'
print('PASS: 围栏校验失败替换为失败文本（零残留）')

# chart 围栏内非 SVG（伪代码）→ 原样保留，不处理不标注
pseudo_md = '```chart\n这不是 SVG，是伪代码示例\n```'
with tempfile.TemporaryDirectory() as tmp:
    with mock.patch('app.services.chart_renderer._render_png', return_value=PNG):
        out = render_charts(pseudo_md, tmp)
    assert '```chart' in out, f'非图表围栏应原样保留: {out}'
    assert '这不是 SVG' in out, out
    assert '图表生成失败' not in out, f'非图表围栏不应出失败标注: {out}'
    assert not os.path.exists(os.path.join(tmp, 'assets', 'chart_1.png')), '非图表围栏不应写 chart 文件'
print('PASS: chart 围栏内非 SVG 原样保留不处理')

# ── svg 字面量垃圾清理（保留：只作用于图片引用后粘连的 svg 字面量，不触碰用户内容）──
garbage_md = '```chart\n' + good_svg + '\n```\n![图表](assets/chart_1.png)svg'
with tempfile.TemporaryDirectory() as tmp:
    with mock.patch('app.services.chart_renderer._render_png', return_value=PNG):
        out = render_charts(garbage_md, tmp)
    assert '![图表](assets/chart_1.png)' in out, out
    assert '![图表](assets/chart_1.png)svg' not in out, f'粘连 svg 应被清理: {out}'
print('PASS: 图片引用后粘连 svg 字面量清理')

# 场景：图片引用后跨行跟 svg 单词（正常文本）→ 不被误清理
safe_md = '```chart\n' + good_svg + '\n```\n![图表](assets/chart_1.png)\n\nsvg 文件说明'
with tempfile.TemporaryDirectory() as tmp:
    with mock.patch('app.services.chart_renderer._render_png', return_value=PNG):
        out = render_charts(safe_md, tmp)
    assert '![图表](assets/chart_1.png)' in out, out
    assert 'svg 文件说明' in out, f'跨行 svg 单词不应被误清理: {out}'
print('PASS: 跨行 svg 单词不被误清理')

# ── 2026-08-16 resvg→PIL 真实渲染回归：文字 + 连接线（path 虚线）必须渲染 ──
# resvg 全系在 Windows 无法渲染 SVG 文字（上游 bug：usvg "No match for <font>"），
# PIL 自绘渲染器接管后必须保证：① 中文文字渲染 ② path 虚线（粘连命令 M60 70 V120）
# 画出来。不 mock _render_png，走真实渲染（PIL 是硬依赖，测试环境可用）。
from io import BytesIO
from PIL import Image
from app.services.chart_renderer import _render_png

real_svg = '''<svg viewBox="0 0 320 200" width="320" height="200">
<rect width="100%" height="100%" fill="#f5f5f5"/>
<rect x="10" y="20" width="100" height="50" rx="6" fill="#ffffff" stroke="#2d3142"/>
<text x="60" y="50" font-size="16" text-anchor="middle" fill="#2d3142">文档</text>
<path d="M60 70 V120 H160" fill="none" stroke="#4f5d75" stroke-dasharray="4 4"/>
<rect x="160" y="120" width="100" height="50" rx="6" fill="#ffffff" stroke="#2d3142"/>
<text x="210" y="150" font-size="16" text-anchor="middle" fill="#2d3142">检索</text>
</svg>'''
data = _render_png(real_svg)
assert data and data.startswith(b'\x89PNG'), f'真实渲染应返回 PNG bytes，实际 {type(data)}'
im = Image.open(BytesIO(data)).convert('RGB')
px = im.load()
w, h = im.size
# 文字条带（2x：rect1 y∈[40,180]，文字基线 y=50→100，取节点内避框线）
text_dark = sum(1 for xx in range(40, 200) for yy in range(60, 110)
                if sum(px[xx, yy]) < 300)
assert text_dark > 0, f'PIL 渲染应画出文字（用户实测 resvg 全系为 0），实际 {text_dark} == 0'
# path 虚线竖线（2x：M60 70 V120 → x=120→240, y=140..240；粘连命令 tokenizer 必须解析）
path_dark = sum(1 for xx in range(230, 250) for yy in range(140, 250)
                if sum(px[xx, yy]) < 300)
assert path_dark > 0, f'path 虚线（粘连 M60 70 V120）应画出竖线，实际 {path_dark} == 0'
print(f'PASS: PIL 真实渲染 — 文字 {text_dark}px + path 虚线竖线 {path_dark}px')

# ── 2026-08-16 贝塞尔曲线（C/S/Q/T）必须渲染 ──
# 此前曲线命令跳过参数漏画（图表连接线用曲线时消失）。补齐：C/S 三次采样、
# Q/T 二次采样（S/T 控制点反射），虚线按弧长分段。用户实际 SVG 曲线连线已验。
curve_svg = '''<svg viewBox="0 0 320 160" width="320" height="160">
<rect width="100%" height="100%" fill="#f5f5f5"/>
<path d="M20 80 C 60 40, 120 120, 160 80" stroke="#eb6c36" stroke-width="2" fill="none"/>
<path d="M180 80 Q 210 50, 240 80" stroke="#eb6c36" stroke-width="2" fill="none"/>
<path d="M20 130 S 60 100, 90 130 T 140 130" stroke="#4f5d75" stroke-width="2" fill="none"/>
<text x="160" y="150" font-size="12" fill="#2d3142">曲线</text>
</svg>'''
data = _render_png(curve_svg)
assert data and data.startswith(b'\x89PNG'), f'曲线渲染应返回 PNG bytes，实际 {type(data)}'
im3 = Image.open(BytesIO(data)).convert('RGB')
px3 = im3.load()
# 曲线条带：C x∈[40,160] y∈[60,120] + Q x∈[180,240] + S/T x∈[20,140] y∈[100,135]（×2 后）
# 文字在 (160,150)→(320,300)，不在本区域；阈值 600 含强调色 #eb6c36(397) 与主色 #4f5d75(289)
curve_dark = sum(1 for xx in range(40, 480) for yy in range(80, 272)
                 if sum(px3[xx, yy]) < 600)
assert curve_dark > 100, f'贝塞尔曲线（C/Q/S/T）应渲染出折线，实际 {curve_dark}px'
print(f'PASS: 贝塞尔曲线 C/Q/S/T 渲染 — 有色 {curve_dark}px')

# ── 2026-08-16 polygon 描边闭合（用户实测「流程图错位和边框缺失」）──
# draw.line 不自动闭合 polygon——此前菱形/箭头描边缺「最后一点回起点」的边
# （流程图菱形只剩 3 条边，视觉残缺）。回归：菱形闭合边（左顶点→顶顶点）
# 必须画出。文字放菱形下方避免污染检测区域。
poly_svg = '''<svg viewBox="0 0 120 140" width="120" height="140">
<rect width="100%" height="100%" fill="#f5f5f5"/>
<polygon points="60,20 100,60 60,100 20,60" fill="#ffffff" stroke="#eb6c36"/>
<text x="60" y="128" font-size="12" text-anchor="middle" fill="#2d3142">判断</text>
</svg>'''
data = _render_png(poly_svg)
assert data and data.startswith(b'\x89PNG'), f'polygon 渲染应返回 PNG bytes，实际 {type(data)}'
im4 = Image.open(BytesIO(data)).convert('RGB')
px4 = im4.load()
# 闭合边 = 左顶点(20,60)→顶顶点(60,20) 的斜边；区域 [26,56]×[26,56]（SVG）×2
# 只含这条斜带（顶→右 x≥60、底→左 y≥60 不在区域内；文字 y=128 下方）
close_dark = sum(1 for xx in range(52, 112) for yy in range(52, 112)
                 if sum(px4[xx, yy]) < 600)
assert close_dark > 0, f'polygon 闭合边（左顶点→顶顶点）应画出，实际 {close_dark} == 0'
print(f'PASS: polygon 描边闭合 — 闭合边 {close_dark}px')

# ── 2026-08-17 transform + dominant-baseline 补齐（用户实测「图表错位/缺内容」）──
# ① <g transform="translate(...)"> 内元素此前整块不画（_svg_to_pil 直接跳过 g）；
# ② 文字默认当基线画，dominant-baseline=middle（节点内垂直居中）被忽略 → 偏下。
# 回归：平移后的连线/标签出现在移动区、原位为空；居中文字中线贴近节点中线。
from app.services.chart_renderer import _svg_to_pil, _detect_chinese_font

tr_svg = '''<svg viewBox="0 0 640 300" width="640" height="300">
<rect width="100%" height="100%" fill="#f5f5f5"/>
<g transform="translate(120,60)">
  <line x1="0" y1="0" x2="180" y2="6" stroke="#4f5d75" stroke-width="2"/>
  <text x="90" y="14" font-size="16" text-anchor="middle" fill="#4f5d75">WRITE / API</text>
</g>
<rect x="60" y="170" width="150" height="60" rx="6" fill="#ffffff" stroke="#2d3142"/>
<text x="135" y="200" font-size="16" text-anchor="middle" fill="#2d3142"
      dominant-baseline="middle">用户 请求</text>
</svg>'''
im5 = _svg_to_pil(tr_svg, 2.0, _detect_chinese_font())
px5 = im5.load()
# translate 生效：标签/连线画在平移后（SVG x∈[120,300]→像素 x∈[240,600]）；原位（x<120，SVG）空
tr_shift = sum(1 for xx in range(240, 620) for yy in range(110, 170)
               if sum(px5[xx, yy]) < 600)
tr_origin = sum(1 for xx in range(0, 160) for yy in range(110, 170)
                if sum(px5[xx, yy]) < 600)
assert tr_shift > 50, f'transform translate 后区域应画出连线/标签，实际 {tr_shift}px'
assert tr_origin < 5, f'transform 未生效，内容画回原位: {tr_origin}px'
# dominant-baseline=middle 生效：节点 rect y∈[170,230]→像素[340,460] 中线=400，
# 文字 bbox 中线应在 400 附近（基线画法会偏 ~20px）
ys = [yy for xx in range(120, 300) for yy in range(360, 470) if sum(px5[xx, yy]) < 300]
assert ys, '节点内应检出文字像素'
mid = (min(ys) + max(ys)) / 2
assert abs(mid - 400) < 16, f'dominant-baseline 文字中线 {mid:.0f} 应贴近节点中线 400（偏 {abs(mid-400):.0f}px）'
print(f'PASS: transform translate — 平移区 {tr_shift}px / 原位 {tr_origin}px')
print(f'PASS: dominant-baseline middle — 文字中线 {mid:.0f}（节点中线 400）')

# ── 2026-09-19 fig 语义围栏（LLM 只写语义 JSON，坐标由 chart_layout 算）──
# 与 chart 分支的关键差异：fig 是毫无歧义的协议标记 → 任何失败都必须零残留替换
# （chart 内非 SVG 是原样保留的，两者有意不同）。

import json as _json

FIG_HCHAIN = '{"layout":"hchain","title":"流程","nodes":[{"label":"输入"},{"label":"输出"}]}'
FIG_GRID = ('{"layout":"grid","title":"依赖","rows":[["前端"],["接口层"]],'
            '"edges":[{"from":[0,0],"to":[1,0],"label":"调用"}]}')


def _fig(body):
    return '```fig\n' + body + '\n```'


with tempfile.TemporaryDirectory() as tmp:
    with mock.patch('app.services.chart_renderer._render_png', return_value=PNG):
        out = render_charts(_fig(FIG_HCHAIN), tmp)
    assert '![图表](assets/chart_1.png)' in out, out
    assert '```fig' not in out, f'围栏应被替换消失（零残留）: {out}'
    assert '{' not in out, f'语义 JSON 不应残留在产物里: {out}'
    ads = os.path.join(tmp, 'assets')
    assert os.path.exists(os.path.join(ads, 'chart_1.png'))
    assert os.path.exists(os.path.join(ads, 'chart_1.svg')), 'fig 也应保存 SVG 片段'
    # chart_N.json 只在走通 fig 分支时落盘 —— 是「模型确实切到新协议」的确定性证据
    # （两条路径都出 PNG 且张数可能一样，只有 .json 能区分）
    jp = os.path.join(ads, 'chart_1.json')
    assert os.path.exists(jp), 'fig 路径应落 chart_N.json'
    with open(jp, encoding='utf-8') as f:
        assert _json.load(f)['layout'] == 'hchain', 'json 应为原始语义源'
print('PASS: fig 语义围栏 → PNG + SVG + JSON 三落盘，围栏/JSON 零残留')


# 带 edges 的 grid（v1 唯一任意拓扑连线能力）走通全链
with tempfile.TemporaryDirectory() as tmp:
    with mock.patch('app.services.chart_renderer._render_png', return_value=PNG):
        out = render_charts(_fig(FIG_GRID), tmp)
    assert '![图表](assets/chart_1.png)' in out, out
    assert '```fig' not in out, out
print('PASS: fig grid（含 edges 拓扑连线）全链通过')

# 容错：模型改用 fig 围栏却仍写 SVG → 按**内容形态**分流到 chart 路径
# （围栏名与内容解耦，legacy 兜底重新生效；代价是这张图不落 .json）
with tempfile.TemporaryDirectory() as tmp:
    with mock.patch('app.services.chart_renderer._render_png', return_value=PNG):
        out = render_charts(_fig(good_svg), tmp)
    ads = os.path.join(tmp, 'assets')
    assert '![图表](assets/chart_1.png)' in out, f'fig 内嵌 SVG 应按 chart 路径出图: {out}'
    assert os.path.exists(os.path.join(ads, 'chart_1.svg'))
    assert not os.path.exists(os.path.join(ads, 'chart_1.json')), \
        '走 chart 容错路径时不应落 .json（这正是区分两条路径的信号）'
print('PASS: fig 围栏内嵌 <svg> → 按 chart 路径处理（容错兜底生效，不落 .json）')

# JSON 损坏 / 未知 layout → 失败文本，零残留不写文件
for label, body in (('JSON 语法错', '{这不是 JSON'), ('未知 layout', '{"layout":"tree","nodes":[{"label":"x"}]}'),
                    ('空对象', '{}'), ('非对象', '[1,2,3]')):
    with tempfile.TemporaryDirectory() as tmp:
        with mock.patch('app.services.chart_renderer._render_png', return_value=PNG):
            out = render_charts(_fig(body), tmp)
        assert '```fig' not in out, f'{label}: 围栏应零残留: {out}'
        assert body[:6] not in out, f'{label}: 原始 JSON 不应残留: {out}'
        assert '📊 图表生成失败' in out, f'{label}: 应替换为失败文本: {out}'
        assert '![图表](assets/' not in out, f'{label}: 不应产生图片引用: {out}'
        assert not os.path.exists(os.path.join(tmp, 'assets', 'chart_1.png')), \
            f'{label}: 失败不应写任何文件'
print('PASS: fig 的 JSON 损坏 / 未知 layout / 空对象 / 非对象 → 失败文本零残留')

# 未选图表 / 不是专用围栏 → 仍不处理（协议隔离不因新增 fig 而放松）
with tempfile.TemporaryDirectory() as tmp:
    with mock.patch('app.services.chart_renderer._render_png', return_value=PNG):
        out = render_charts('```json\n' + FIG_HCHAIN + '\n```', tmp)
    assert '```json' in out and 'layout' in out, f'通用 json 围栏不该被碰: {out}'
    assert not os.path.exists(os.path.join(tmp, 'assets', 'chart_1.png'))
print('PASS: 通用 ```json 围栏不被处理（协议隔离保持）')

# 多图：fig / chart 交替 → 按文档顺序编号 1/2/3（两条路径共用计数器）
multi = '\n\n'.join([_fig(FIG_HCHAIN), '```chart\n' + good_svg + '\n```', _fig(FIG_GRID)])
with tempfile.TemporaryDirectory() as tmp:
    with mock.patch('app.services.chart_renderer._render_png', return_value=PNG):
        out = render_charts(multi, tmp)
    ads = os.path.join(tmp, 'assets')
    for i in (1, 2, 3):
        assert os.path.exists(os.path.join(ads, f'chart_{i}.png')), f'缺 chart_{i}.png'
    assert not os.path.exists(os.path.join(ads, 'chart_4.png'))
    assert out.count('![图表](assets/chart_') == 3, f'应产生 3 个图片引用: {out}'
    assert os.path.exists(os.path.join(ads, 'chart_1.json'))     # fig
    assert not os.path.exists(os.path.join(ads, 'chart_2.json'))  # chart
    assert os.path.exists(os.path.join(ads, 'chart_3.json'))     # fig
    assert '```fig' not in out and '```chart' not in out, '两种围栏都应零残留'
print('PASS: fig/chart 交替多图 → chart_1/2/3 顺序编号，.json 精确标记各图来源路径')

# 中间一张失败 → 编号不跳号（失败不占位）
with tempfile.TemporaryDirectory() as tmp:
    with mock.patch('app.services.chart_renderer._render_png', return_value=PNG):
        out = render_charts('\n\n'.join([_fig(FIG_HCHAIN), _fig('{坏 JSON'), _fig(FIG_GRID)]), tmp)
    ads = os.path.join(tmp, 'assets')
    assert os.path.exists(os.path.join(ads, 'chart_1.png'))
    assert os.path.exists(os.path.join(ads, 'chart_2.png')), '失败不占号，第三张应为 chart_2'
    assert not os.path.exists(os.path.join(ads, 'chart_3.png'))
    assert out.count('图表生成失败') == 1
print('PASS: fig 失败不跳号（1 成功 / 2 失败 / 3 成功 → chart_1 + chart_2）')

# render_chart=False（polished 侧）对 fig 同样不渲染
with tempfile.TemporaryDirectory() as tmp:
    out = _post_process(_fig(FIG_HCHAIN), ['chart'], None, 'text', tmp, render_chart=False)
    assert '```fig' in out, 'polished 侧应保留原围栏不动'
    assert not os.path.exists(os.path.join(tmp, 'assets', 'chart_1.json'))
print('PASS: render_chart=False 对 fig 同样不渲染（双产物门控一致）')

# ── _has_chart_fence：两种协议都算「已出图」──
from app.routers.generate import _has_chart_fence

assert _has_chart_fence('```fig\n{}')
assert _has_chart_fence('```chart\n<svg/>')
assert _has_chart_fence(_fig(FIG_HCHAIN))
assert not _has_chart_fence('```svg\n<svg/>')
assert not _has_chart_fence('```json\n{}')
assert not _has_chart_fence('')
assert not _has_chart_fence(None)
assert not _has_chart_fence('> 图表说明：内容不适合图表化')
print('PASS: _has_chart_fence — fig/chart 皆判为已出图，svg/json/空/逃生口皆不误判')

# ── _gen_timeout：勾选图表放宽总超时（Phase 4）──
from app.routers.generate import _gen_timeout, _GEN_TIMEOUT_BASE, _GEN_TIMEOUT_CHART

assert _GEN_TIMEOUT_CHART > _GEN_TIMEOUT_BASE, '图表档必须比基础档宽'
assert _gen_timeout(['chart']) == _GEN_TIMEOUT_CHART
assert _gen_timeout(['chart', 'toc']) == _GEN_TIMEOUT_CHART
assert _gen_timeout('toc,chart') == _GEN_TIMEOUT_CHART, '流式入口传的是逗号分隔串，必须同样放宽'
assert _gen_timeout(['toc']) == _GEN_TIMEOUT_BASE
assert _gen_timeout('toc,summary') == _GEN_TIMEOUT_BASE
assert _gen_timeout(None) == _GEN_TIMEOUT_BASE
assert _gen_timeout([]) == _GEN_TIMEOUT_BASE
assert _gen_timeout('') == _GEN_TIMEOUT_BASE
assert _gen_timeout(['chartboard']) == _GEN_TIMEOUT_BASE, '必须精确匹配 format key，不能子串误判'
print('PASS: _gen_timeout 按 formats 分档（list/逗号串/None/空/子串）')

# ⚠️ AsyncOpenAI 是**惰性构造**的：超时值真正生效的地方是 client property，不是 __init__。
# 只改 __init__ 会「存了不用」——值进了实例属性，property 里仍写死 330，超时一点没变，
# 而上面那组 _gen_timeout 断言照样全 PASS。所以这里直接盯住构造实参。
from unittest import mock as _mock
from app.llm.service import LLMService as _LLMService

with _mock.patch('app.llm.service.AsyncOpenAI') as _oa:
    _svc = _LLMService({'llm': {'api_key': 'sk-test-000', 'provider': 'deepseek'}},
                       request_timeout=_GEN_TIMEOUT_CHART)
    _ = _svc.client
    assert _oa.call_args.kwargs['timeout'] == _GEN_TIMEOUT_CHART, (
        'client property 未读取实例上的 request_timeout（惰性构造处写死了默认值）')

with _mock.patch('app.llm.service.AsyncOpenAI') as _oa:
    _ = _LLMService({'llm': {'api_key': 'sk-test-000', 'provider': 'deepseek'}}).client
    assert _oa.call_args.kwargs['timeout'] == _GEN_TIMEOUT_BASE, '不传时仍是基础档（其余 9 处调用点零影响）'
print('PASS: request_timeout 落到 client 构造实参（惰性 property 未写死）')

print('Chart renderer tests done')
