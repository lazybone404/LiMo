"""
导出主题跟随（2026-10-16）—— 六轴参数 → `<html>` 属性 / 主题层注入 / 零回归

**这件事要解决什么**：`HTML_THEMES["limo"]` 原先是自包含裸色值 CSS，零 `var()`；
两个导出入口一个 accent/base 参数都不传，恒用硬编码蓝 `#2563EB`。
骨架 / 卡片方案 / 逐维度微调 / 标记装饰**全都跟随了，唯独颜色不跟随** ——
直接违背项目写死的核心约束「用户在预览栏看到什么，导出就应该得到什么」。

**为什么这些用例值得存在**（每条都对应一种「不报错、只是结果变一点」的失效）：

  - 轴参数没落到 `<html>`：导出静默用回固定配色，用户只会觉得「颜色怪」；
  - **主题层没注入或注入太晚**：`var(--tk-*)` 全部落空 → 导出**比改造前更糟**
    （原来至少是固定的蓝，现在是无色）；
  - 未跟随路径**多注入**了主题层：给每个历史调用方凭空多一段 `<style>`，
    破坏「无参数产物逐字节零回归」；
  - 白名单漏了 `"`：`data-accent="{v}"` 是**原样拼接的注入面**。

⚠️ 运行：`python tests/test_export_theme.py` 或 `pytest tests/test_export_theme.py`
（断言写在 `def test_*` 里而不是模块级 —— 模块级裸断言在 pytest 下**收集数为 0**，
既不红也不计数，是「6 项全过」的幻觉形态，见 test_export_print.py 的说明）。
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from conftest import normalize_export_nonce
from app.routers.export import (                        # noqa: E402
    HTML_THEMES, _AXIS_VALUE_RE, _TEXTURE_OPACITY_RE, _build_selfcontained_html,
    _load_block_css, _load_card_css, _load_theme_layer_css,
)

MD = "# 标题\n\n正文与 `行内代码`。\n"


def _html_tag(html: str) -> str:
    """`<html …>` 开标签 —— 轴属性写在这上面。

    ⚠️ 与 test_export_blocks.py 的同名工具同款：**不能拿整篇断子串**，
    内联的 CSS 注释里逐字写着这些属性名。
    """
    return html[:html.index('>', html.index('<html')) + 1]


# ═══════════════════════════════════════════════════════════════
# 1. 零参数 = 逐字节零回归
# ═══════════════════════════════════════════════════════════════

def test_零参数_导出与改造前逐字节相同():
    """不带任何轴参数时，`<html>` 标签上**一个属性都不该多**，主题层也不该注入。

    这是「第三方脚本 / 历史调用方零回归」的落点：那条路径根本不知道有新参数。
    """
    zero = _build_selfcontained_html(MD, "标题")
    # ⚠️ 断 `startswith` 而不是 `_html_tag(zero) == …`：那个辅助函数返回的是
    # 「`<html` 之前的一切 + 开标签」，而它前面永远有 `<!DOCTYPE html>\n`。
    assert zero.startswith('<!DOCTYPE html>\n<html lang="zh-CN">')
    # 显式传空串 = 不传（六个参数的默认值就是空串）
    assert normalize_export_nonce(zero) == normalize_export_nonce(
        _build_selfcontained_html(MD, "标题", theme_mode="", accent="", base="",
                                  font="", texture="", texture_opacity=""))
    # 主题层没被注入 —— 判据用主题层里独有的字样（不是 `--tk-`，那是两个来源共有的）
    assert '--accent-hue' not in zero
    # 未跟随 + paper + 无标记 ⇒ 中性令牌也不注入
    assert '--tk-accent:' not in zero


def test_对照_零回归判据抓得住多注入():
    """判据必须自证有区分力：同一条判据喂「跟随」的输出**必须变红**。

    否则「`--accent-hue` 不在输出里」可能只是因为字符串写错了（模式 #38）。
    """
    followed = _build_selfcontained_html(MD, "标题", accent="sky")
    assert '--accent-hue' in followed                       # ← 判据真的在测东西
    assert '--accent-hue' not in _build_selfcontained_html(MD, "标题")


# ═══════════════════════════════════════════════════════════════
# 2. 六轴参数落到 `<html>`
# ═══════════════════════════════════════════════════════════════

def test_五条轴属性原样落到_html_标签上():
    tag = _html_tag(_build_selfcontained_html(
        MD, "标题", theme_mode="dark", accent="rouge", base="warm", font="kaiti",
        texture="xuan"))
    for attr, val in (("data-theme", "dark"), ("data-accent", "rouge"),
                      ("data-base", "warm"), ("data-font", "kaiti"),
                      ("data-texture", "xuan")):
        assert f'{attr}="{val}"' in tag, f"{attr} 没落到 <html> 上：{tag}"


def test_纹理强度走行内变量而不是_data_属性():
    """它不是枚举值而是 0–1 小数，`data-*` 表达不了；且前端传的是**已反转**的值。"""
    tag = _html_tag(_build_selfcontained_html(MD, "标题", texture="xuan",
                                              texture_opacity="0.3"))
    assert 'style="--texture-opacity: 0.3"' in tag
    assert 'data-texture-opacity' not in tag


def test_各轴独立_只传一条也只生效那一条():
    """「非空才写」是逐项的，不是「传了任何一条就写全套」——
    写全套会把默认值也钉进 HTML，于是「某条轴没传」与「传了默认值」再也分不开。"""
    tag = _html_tag(_build_selfcontained_html(MD, "标题", accent="sage"))
    assert 'data-accent="sage"' in tag
    assert 'data-base' not in tag and 'data-theme' not in tag
    assert 'data-font' not in tag and 'data-texture' not in tag


def test_未传纹理强度时不写行内_style():
    """`plain` 前端不发强度；此时 `<html>` 上不该出现空的 style 属性。"""
    tag = _html_tag(_build_selfcontained_html(MD, "标题", texture="plain"))
    assert 'data-texture="plain"' in tag
    assert 'style=' not in tag


# ═══════════════════════════════════════════════════════════════
# 3. 白名单：非法值静默丢弃（`data-accent="{v}"` 是原样拼接的注入面）
# ═══════════════════════════════════════════════════════════════

def test_非法轴值被丢弃且不影响其它轴():
    """某个轴值不对**不改变产物的性质**（判据同 `card_style`）——
    丢那一项、其余照常，既不失败也不写一个坏属性进去。"""
    tag = _html_tag(_build_selfcontained_html(
        MD, "标题", accent='x" onload="alert(1)', base="warm"))
    assert 'onload' not in tag
    assert 'data-accent' not in tag
    assert 'data-base="warm"' in tag        # ← 其余轴不受牵连


def test_对照_白名单抓得住注入_也放得过合法值():
    """双向自证：不是「怎么喂都丢」（那合法性轴反而静默不生效），
    也不是「怎么喂都收」（那是注入面）。"""
    evil = _build_selfcontained_html(MD, "标题", accent='a" onclick="x')
    assert 'onclick' not in evil
    assert 'data-accent' not in _html_tag(evil)

    good = _build_selfcontained_html(MD, "标题", accent="spearmint")
    assert 'data-accent="spearmint"' in _html_tag(good)


def test_轴值与强度的白名单边界():
    """把两条正则的**边界**钉住 —— 它们是唯一的防线，靠「读一眼觉得没问题」不够。"""
    # 合法：小写字母 / 数字 / 连字符
    for v in ("sky", "warm", "spearmint", "xuan", "wenkai", "a1-2"):
        assert _AXIS_VALUE_RE.match(v), v
    # 非法：大写（CSS 属性值区分大小写，`data-accent="Sky"` 永不命中规则 → 静默无色）、
    # 空格 / 引号 / 尖括号 / 斜杠（注入与逃逸所需字符）、超长
    for v in ("Sky", " sky", 'a"b', "a<b", "a>b", "a/b", "a;b", "a b", "a" * 25):
        assert not _AXIS_VALUE_RE.match(v), v

    for v in ("0", "1", "0.3", "0.45", "0.00", "0.1234"):
        assert _TEXTURE_OPACITY_RE.match(v), v
    # `1.5` / `-0.2` 越界（opacity 值域 0–1）；`0.12345` 超过 4 位小数上限
    # （白名单不限长度 = 一次请求可以带几 MB 合法数字进 <style>）
    for v in ("", "1e3", "0.5%", ".5.5", "calc(1)", "1.5", "-0.2", "0.12345", "0."):
        assert not _TEXTURE_OPACITY_RE.match(v), v


def test_前端反转映射的产出确实过得了强度白名单():
    """成对守卫（跨语言）：前端 `lib/texture.ts::textureOpacity` 的产出必须被后端接受。

    ⚠️ 前端那侧固定两位小数（`toFixed(2)`）**正是为了这条** —— 滑条若不是整数步长，
    `String((100-v)/100)` 可能给出 `5e-2` 这类形态，而白名单刻意不接受科学计数法。
    两个实现分居 TS / Python 两侧，只能靠**共享的取值样例**钉住。
    """
    for slider in (10, 25, 50, 73, 99, 100):
        assert _TEXTURE_OPACITY_RE.match(f"{((100 - slider) / 100):.2f}"), slider


# ═══════════════════════════════════════════════════════════════
# 4. 主题层注入闸门
# ═══════════════════════════════════════════════════════════════

def test_跟随时注入主题层_且在主题_css_之前():
    """⚠️ 顺序是**载荷性**的：主题层只声明变量，`HTML_THEMES["limo"]` 消费
    `var(--tk-*)` —— 放后面变量就没定义，`var()` 落空、整条声明作废，
    导出页**比改造前更糟**（原来至少是固定的蓝）。"""
    html = _build_selfcontained_html(MD, "标题", accent="rouge")
    i_layer = html.index('--accent-hue')
    i_theme = html.index('max-width: 860px')       # limo 主题的 body 规则
    assert i_layer < i_theme, "主题层必须排在主题 CSS 之前"


def test_跟随时_中性令牌让位给主题层():
    """`_EXPORT_NEUTRAL_TOKENS_CSS` 与主题层是**同一批令牌的两个提供者**。

    两份同时注入 = 让「跟随时卡片底色该是 #ffffff 还是用户基调的暖白」由
    CSS 源顺序裁决 —— 那正是本轮要消灭的形态（取值差别**就是「跟随」的语义**）。
    """
    followed = _build_selfcontained_html(MD, "标题", accent="rouge", skeleton="card")
    # 主题层在场（它的 `:root` 底盘定义 `--tk-surface`）
    assert '--accent-hue' in followed
    # 中性令牌那份的**独有取值**不在（它的 `--tk-accent` 是 #2563eb 这个固定蓝）
    assert '--tk-accent: #2563eb' not in followed
    # ⚠️ 卡片规则本身当然还在（只是令牌换了来源）—— 判据不能把「让位」误伤成「没卡片」
    assert '.md-card' in followed and 'data-skeleton="card"' in _html_tag(followed)


def test_未跟随_卡片无标记_中性令牌仍在场():
    """对照：固定主题下没有任何轴参数，`cards.css` / `blocks.css` 的 `--tk-*`
    仍必须由中性令牌那份补齐 —— 少了它就是「卡片一点样式都没有」而导出照样成功。"""
    html = _build_selfcontained_html(MD, "标题", skeleton="card")
    assert '--accent-hue' not in html           # 主题层不在
    assert '--tk-accent:' in html               # 但令牌有定义


def test_主题层文件读得到且含两个_media_screen_暗色块():
    """跨文件不变量（与 `test_export_print.py` 的 limo 断言**成对**）：

    **暗色是屏幕概念，纸永远是浅的** —— 主题层里两处 `[data-theme="dark"]`
    （`--tk-*` 底盘 + `--md-*`）都必须包在 `@media screen` 里。
    裸着的话 `@media (prefers-color-scheme: dark)` 与 `@media print` **同时匹配**，
    浅色标题（`--md-h1: …78%`）会落到白纸上；`_PRINT_CSS` 只管 body / 代码容器，
    管不到 h1~h4 / a / strong / blockquote / 表格。
    """
    css = _load_theme_layer_css()
    assert css.strip(), "主题层是空的 —— 读到了空文件（路径对但内容没了）"
    # 剥注释：本文件通篇在讨论 `@media screen`，不剥的话源码注释就能骗过判据
    code = re.sub(r"/\*[\s\S]*?\*/", "", css)
    assert '@media screen' in code
    assert '@media (prefers-color-scheme: dark)' not in code, \
        "主题层里有裸的暗色媒体查询 —— 它会落到打印上（见 docstring）"
    # 自证：暗色块真的在里面（不是「删光了所以第二条通过」）
    assert code.count('[data-theme="dark"]') >= 2, "主题层的暗色块不见了"


def test_对照_暗色守卫抓得住裸媒体查询():
    """同一条判据喂违规输入必须变红 —— 否则「没有裸的暗色段」可能只是字符串写错了。"""
    bad = '@media (prefers-color-scheme: dark) { :root { --tk-surface: #1c1c1e; } }'
    assert '@media (prefers-color-scheme: dark)' in bad
    good = '@media screen and (prefers-color-scheme: dark) { :root { x: 1; } }'
    assert '@media (prefers-color-scheme: dark)' not in good
    assert '@media screen' in good


# ═══════════════════════════════════════════════════════════════
# 5. 主题层是 blocks.css / cards.css 的**超集**（跟随时那批令牌的来源）
# ═══════════════════════════════════════════════════════════════

def test_主题层覆盖_blocks_css_与_cards_css_引用的全部令牌():
    """与 `test_export_blocks.py` 的成对常量守卫**成对**：那条走「未跟随」
    （令牌由 `_EXPORT_NEUTRAL_TOKENS_CSS` 提供），这条走「跟随」（由主题层提供）。

    ⚠️ 只测一条的话，另一条路径上「新引了一个 `--tk-*` 而提供者没补」会静默失效 ——
    症状是「卡片/块少一块样式」，不报错、应用内预览里却完全正常。
    """
    layer = re.sub(r"/\*[\s\S]*?\*/", "", _load_theme_layer_css())
    used = set()
    for css in (_load_block_css(), _load_card_css()):
        used |= set(re.findall(r"var\((--tk-[a-z-]+)", re.sub(r"/\*[\s\S]*?\*/", "", css)))
    assert used, "没抽到任何 --tk-* —— 判据本身失效了（正则/文件改了？）"
    # `--tk-x:` 是**定义**形态；`var(--tk-x)` / `var(--tk-x, …)` 都不是
    missing = sorted(t for t in used if f"{t}:" not in layer)
    assert missing == [], f"跟随路径缺少这些令牌的定义（主题层里没有）：{missing}"


# ═══════════════════════════════════════════════════════════════
# 6. 固定主题不受影响
# ═══════════════════════════════════════════════════════════════

def test_固定主题不消费_tk_变量():
    """`paper` / `github` / `minimal` 是自包含裸色值 CSS —— 它们**不该**跟随。

    用户显式选了「学术论文」样式，那就该拿到论文样式；此时哪怕带了轴属性，
    这些主题里也没有 `var()` 去消费它们（跟随是 `limo` 这一档的语义）。
    """
    for name in ("paper", "github", "minimal"):
        css = HTML_THEMES[name]
        assert 'var(--tk-' not in css, f"{name} 主题开始消费 --tk-* 了 —— 它会跟着应用配色走"


def test_固定主题自身仍完整_暗色与令牌都不依赖外部():
    """对照：`limo` 现在**依赖**主题层（未跟随时依赖中性令牌那份），
    而三个固定主题必须**自成一体** —— 它们没有也不该有提供者。"""
    limo = HTML_THEMES['limo']
    assert 'var(--tk-foreground, #1C1C1E)' in limo, \
        "limo 的配色应写成「变量 + 今天裸值」的 fallback 形式（零回归靠 var() 兜底，不靠分支）"
    for name in ("paper", "github", "minimal"):
        assert '#' in HTML_THEMES[name]


print("\nAll export theme-follow tests done")
