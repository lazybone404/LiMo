"""
打印样式测试（导出 HTML 的 `@media print` 行为）

**为什么这些用例值得存在**：打印样式由三处拼起来（`_PRINT_CSS` + Pygments 的
print 段 + limo 主题的 print 段），三者靠 **CSS 源顺序**决定优先级 —— 而顺序是
**隐式契约**：把 `_PRINT_CSS` 挪到 style_block 前面，代码照样跑、页面照样出，
只是打印出来是深底浅字。这类回归没有报错、没有日志，只能靠断言挡住。

同理，`pre`/`table` 上的 `break-inside: avoid` 曾被写进去过，后果是**整页空白** ——
也是一条「删掉就再也想不起来」的约束，故一并固化。

运行：python tests/test_export_print.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.routers.export import (                        # noqa: E402
    HTML_THEMES, _PRINT_CSS, _build_selfcontained_html, _get_pygments_css,
)


def _avoid_selectors(css: str) -> set:
    """收集所有声明了 `break-inside` 的规则的选择器（每个取最后一个简单选择器）。

    ⚠️ 不能写成 `([^{}]+)\\{([^}]*)\\}` 那种「选择器+块」的配对正则 —— `@media print`
    是**嵌套块**，外层配对会把整个 `@media print ` 当成选择器，`pre` 永远扫不到
    （本文件的自证用例正是靠这一点抓出初版实现的错误）。
    改为「定位每个 break-inside，向前回溯到所属规则的头」。

    还要先剥掉注释 —— `_PRINT_CSS` 的注释里就写着 `break-inside:avoid` 这几个字
    （解释为什么 pre/table 不能用），不剥的话会被当成真声明，回溯到上一条规则上去。
    """
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.DOTALL)
    sels = set()
    for m in re.finditer(r'break-inside', css):
        head = css[:m.start()]
        brace = head.rfind('{')
        if brace == -1:
            continue
        prev = max(head.rfind('}', 0, brace), head.rfind('{', 0, brace))
        for s in head[prev + 1:brace].split(','):
            s = s.strip()
            if s:
                sels.add(s.split()[-1])
    return sels


# ═══════════════════════════════════════════════════════════
# 1. 三处打印 CSS 都在
# ═══════════════════════════════════════════════════════════

assert '@page' in _PRINT_CSS, "缺少 @page —— 纸张尺寸与页边距没有设置"
assert '@media print' in _PRINT_CSS
assert 'max-width: none' in _PRINT_CSS, \
    "缺少解除屏幕布局的 max-width —— 纸上会出现一圈古怪的窄边距"
print("PASS: _PRINT_CSS 含 @page + 解除屏幕布局")

pygments_css = _get_pygments_css()
if pygments_css:
    assert '@media print' in pygments_css, \
        "Pygments CSS 没有打印段 —— token 会保持 one-dark 浅色，在白纸上不可读"
    _print_seg = pygments_css[pygments_css.index('@media print'):]
    assert '.codehilite' in _print_seg, \
        "Pygments 打印段没有 .codehilite 规则 —— token 配色（类选择器）压不住，必须整份换"
    print("PASS: Pygments 有独立的打印 token 配色")
else:
    print("SKIP: Pygments 未安装")

limo = HTML_THEMES['limo']
assert '@media (prefers-color-scheme: dark)' in limo and '@media print' in limo
assert limo.index('@media print') > limo.index('@media (prefers-color-scheme: dark)'), \
    "limo 的 @media print 必须排在暗色段之后 —— 两者不互斥，靠顺序决定谁胜"
print("PASS: limo 主题在暗色段之后还原了打印取值")


# ═══════════════════════════════════════════════════════════
# 2. 优先级：_PRINT_CSS 必须垫底
# ═══════════════════════════════════════════════════════════

html = _build_selfcontained_html(
    "# 标题\n\n正文 `行内代码` 与 $a^2$。\n\n```python\nx = 1\n```\n", "打印测试")

# 主题 CSS 的屏幕特征串 —— limo 的 body 版心宽度
i_theme = html.index('max-width: 860px')
i_page = html.index('@page')
assert i_page > i_theme, \
    "打印样式出现在主题 CSS 之前 —— 媒体查询不加特异性，同特异性后者胜，会压不住屏幕样式"
print("PASS: 打印样式排在主题 CSS 之后（源顺序即优先级）")


# ═══════════════════════════════════════════════════════════
# 3. pre / table 不得用 break-inside: avoid
# ═══════════════════════════════════════════════════════════

# ── 自证：检测逻辑对真实违规输入必须报出来 ──
_bad = "@media print { pre, tr { break-inside: avoid; page-break-inside: avoid; } }"
assert 'pre' in _avoid_selectors(_bad), "检测逻辑是死的 —— 连故意构造的违规都抓不到"
assert 'tr' in _avoid_selectors(_bad)
print("PASS: break-inside 检测自证有效")

_avoid = _avoid_selectors(_PRINT_CSS)
assert 'pre' not in _avoid, \
    "pre 不能加 break-inside: avoid —— 长度无上界，装不进当页会被整体推到下页，留下一整页空白"
assert 'table' not in _avoid, "table 同理（长表格靠 thead 重复表头，而非整体不跨页）"
assert 'li' in _avoid and 'tr' in _avoid, "行/项级原子性应当保留"
assert 'h1' not in _avoid, \
    "h1~h6 用的是 break-after（标题不与后续正文分离），不该出现在 break-inside 集合里 —— 出现即说明注释没被剥掉"
print(f"PASS: break-inside: avoid 只用于行/项级元素 {sorted(_avoid)}")


print("\nAll export print tests done")
