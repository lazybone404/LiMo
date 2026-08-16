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
big_svg = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100" width="50000" height="50000">
  <rect x="10" y="10" width="80" height="50" fill="#4A90D9"/>
</svg>'''
ok, reason = _validate_svg(big_svg)
assert not ok, f'超大 width 应校验失败，实际通过: {reason}'
assert '画布超限' in reason, reason
print('PASS: P0-2 超大 width 属性被拦截')

# 正常尺寸（width + viewBox 均 ≤640）→ 通过
good_svg = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100" width="400" height="300">
  <rect x="10" y="10" width="80" height="50" fill="#4A90D9"/>
</svg>'''
ok, reason = _validate_svg(good_svg)
assert ok, f'正常尺寸应通过校验: {reason}'
print('PASS: P0-2 正常尺寸校验通过')

# 仅 viewBox、无 width/height（属性缺失 → 回退 viewBox 校验）→ 通过
vb_only_svg = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100">
  <rect x="10" y="10" width="80" height="50" fill="#4A90D9"/>
</svg>'''
ok, reason = _validate_svg(vb_only_svg)
assert ok, f'仅 viewBox 应回退校验并通过: {reason}'
print('PASS: P0-2 仅 viewBox 回退校验')

# ── chart 围栏正常渲染（mock resvg，不依赖打包环境）──

def _chart_md():
    return '```chart\n' + good_svg + '\n```'

with tempfile.TemporaryDirectory() as tmp:
    with mock.patch('app.services.chart_renderer._render_png', return_value=PNG):
        out = render_charts(_chart_md(), tmp)
    assert '![图表](assets/chart_1.png)' in out, out
    assert '```chart' not in out, f'围栏应被替换消失（零残留）: {out}'
    assert os.path.exists(os.path.join(tmp, 'assets', 'chart_1.png'))
print('PASS: chart 围栏渲染 PNG 并替换引用')

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

print('Chart renderer tests done')
