"""P0-03 — md 公式「渲染为图片」通道已彻底移除（源码级守卫）。

**背景**：该选项依赖 matplotlib，而 matplotlib **不在依赖里**。`_render_formulas_to_images`
里那句 `except ImportError: return md` 让它从上线第一天起就静默回退到「保留原文」——
用户选了「渲染为图片」、拿到的是 LaTeX 原文，两侧零报错。文档索引里这条曾记为
「待修：md 公式渲染修复」，一度倾向的修法是「切到 KaTeX 图片管线」，但那个前提不成立：
`_render_formulas_katex` 是给 **HTML 导出**用的，靠 `_get_katex_assets()` 把 katex.min.js/css
注进去、由 **WebView 在客户端**渲染 —— 服务端拿不到 SVG/PNG。

故唯一自洽的选择是**移除通道**，而不是补一个 2GB 级的依赖去救一个「保留原文」也能接受的选项。

⚠️ **半删比不删更糟**：`_latex_to_png` 里的 `plt` 会失去定义 → `NameError`。四处落点
必须一起清：函数体 / 请求模型字段取值 / 4 对调用点 / 前端选项与类型。

⚠️ 本文件是**源码级**断言（读文件比对），不是行为断言 —— 因为要防的正是
「某个调用点漏改」这种静态事实，而它只在**特定请求组合**（fmt=md/html 且
formula_md=render）下才炸；行为测试跑不到全部组合。

对照实验（证明本文件有区分力）：
  A. 把任一处 `formula_md == "render"` 分派加回 → 第 1 组红
  B. 把 `<option value="render">` 加回前端 → 第 2 组红
  C. 连带删掉 `formula_md == "remove"` → 第 3 组红（防「删多了」）
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
_BACK = os.path.join(_ROOT, 'backend', 'app', 'routers', 'export.py')
_FRONT = os.path.join(_ROOT, 'frontend', 'src', 'components', 'ui', 'ExportPanel.tsx')


def _read(path: str) -> str:
    with open(path, encoding='utf-8') as f:
        return f.read()


back = _read(_BACK)
front = _read(_FRONT)

# ── 1. 后端：render 分支与它的依赖整体不存在 ──
assert 'matplotlib' not in back, \
    'export.py 仍引用 matplotlib —— render 分支没删干净（它不在依赖里，永远走 ImportError 兜底）'
assert '_render_formulas_to_images' not in back, '公式渲染函数仍在'
assert '_latex_to_png' not in back, '内部的 _latex_to_png 仍在（半删会 NameError）'
assert 'formula_md == "render"' not in back, '仍有调用点按 render 取值分派'
print('PASS: 后端 render 通道已整体移除（函数 + 调用点 + matplotlib）')

# ── 2. 前端：选项与类型都不再含 render ──
assert 'value="render"' not in front, '导出面板仍有「公式：渲染为图片」选项'
assert "'keep' | 'render' | 'remove'" not in front, 'formulaMd 的类型仍含 render'
assert "formulaMd === 'render'" not in front, '摘要文案仍为 render 取值分支'
print('PASS: 前端选项与类型已移除 render')

# ── 3. 保留通道完好（删的是 render，不是整个公式处理）──
assert '_remove_formulas' in back and 'formula_md == "remove"' in back, \
    '公式「移除」通道被误删 —— 只该删 render'
assert 'formula_md' in back and "formula_md: str" in back, '请求模型的 formula_md 字段被误删'
assert "'keep' | 'remove'" in front, '前端 formulaMd 类型被误删'
assert 'formula_md: formulaMd' in front, '前端请求体不再传 formula_md'
print('PASS: keep / remove 两条通道完好（没有删多）')

print('\n全部 PASS: test_export_formula（P0-03）')


# ═══════════════════════════════════════════════════════════
# pytest 适配层
# ═══════════════════════════════════════════════════════════
# ⚠️ 本文件是**脚本式**的：上面全部断言在**导入期**顺序执行，任一失败即
# `AssertionError` 中断导入 —— `python tests/test_export_formula.py` 与 `pytest`
# 两种跑法都因此变红。
#
# 那为什么还要有这个函数？因为 `scripts/check-all` 跑的是 `pytest tests/ -q`，
# 而**模块级裸断言不产生任何用例**：文件被 pytest 当成普通模块、收集数为 0，
# 于是它既不红也不计数 —— 「3 项全过」是个幻觉，实际一项都没跑
# （2026-10-11 代码审查查出，与 2026-10-06 在另外四个文件上踩的是同一个坑）。
#
# 哨兵的作用：若哪天有人把上面整段包进 `def main()` 却忘了调用，
# `_SUITE_COMPLETED` 就不会被定义 → 本函数 `NameError` → 红。
_SUITE_COMPLETED = True


def test_export_formula_suite():
    """占位入口 —— 断言在模块级（导入期已执行）。函数体刻意为空。"""
    assert _SUITE_COMPLETED

