"""`assets/` 图片引用正则的单一真相源（P2-18）。

运行：./.venv/Scripts/python.exe -m pytest tests/test_markdown_assets.py -q

**修复的是什么**：`![alt](assets/xxx)` 这一形态曾在 **4 处**各写一份正则
（`storage/store.py` / `generation/synthesis.py` / `routers/export.py` /
`generation/content_preparer.py`），且 store 那份的**捕获组布局与另三份不同**
（`assets/` 放在组外）。布局差异不会报错——只表现为某一侧改完后文件名多一段或少一段。

⚠️ 本文件守四件事：
  ① **逐字节等价**：收敛后的输出与收敛前的旧实现**完全相同**（不是「差不多」）
  ② **正/反成对**：`rewrite_asset_urls` ∘ `unrewrite_asset_urls` = 恒等（模式 #52）
  ③ **同一编译对象**：4 个调用点必须 import **同一个** `ASSET_REF_RE`
     （`content_preparer` / `export` 用 `m.re is <正则>` 判分支，各自 compile 时 `is` 恒假）
  ④ **零内联**：全仓不得再长出第五份通用版（AST 守卫，模式 #54）
"""

import ast
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.core.markdown_assets import (
    ASSET_REF_RE, asset_rel_path, rewrite_asset_urls, unrewrite_asset_urls,
)

_BACKEND = Path(__file__).resolve().parent.parent
_APP = _BACKEND / "app"

# 通用版的**特征子串**（`assets/` 后跟「非空白/非右括号」的任意字符）。
# ⚠️ 判据必须选这个特征而不是 `assets/`——后者会误伤 `ocr_preprocessor.py` 里
# **刻意更严**的那份（限定 `img_/fig_` + 4 位数字），以及 export 的 `/api/assets/…`。
_GENERIC_MARK = r"assets/[^\s)]+"

# 收敛后**应当**持有正则对象 `ASSET_REF_RE` 的调用点——
# `content_preparer` / `export` 用 `m.re is ASSET_REF_RE` 判分支，身份比较要求同一对象
_IMPORTERS_RE = (
    "app/generation/synthesis.py",
    "app/routers/export.py",
    "app/generation/content_preparer.py",
)

# `store.py` 只 re-export **函数**（`rewrite_asset_urls`），不持有正则对象——
# 它有 5 个外部调用点，改签名会波及它们；这是刻意的兼容层，不是漏收敛
_STORE_REEXPORT = "app/storage/store.py"


# ═══════════════════════════════════════════════════════════════
# ① 逐字节等价（对照收敛前的旧实现）
# ═══════════════════════════════════════════════════════════════

def _legacy_store_impl(content: str, tx_id: str) -> str:
    """`store.py:36-44` 收敛前的**逐字**复刻（含它那份「组2 = 纯文件名」的布局）。"""
    pat = re.compile(r'!\[([^\]]*)\]\(assets/([^\s)]+)\)')
    return pat.sub(rf'![\1](/api/assets/{tx_id}/\2)', content)


_SAMPLES = [
    '![a](assets/img_0001.png)',
    '![](assets/x.png)',
    '![图](assets/子目录/img_0002.jpg)',          # 子目录：组外/组内布局的差异点
    '前文 ![a](assets/a.png) 中间 ![b](assets/b.png) 后文',
    '![a](/api/assets/other/y.png)',               # 已是绝对 URL：不该动
    '![a](data:image/png;base64,AAAA)',            # data URI：不该动
    '![a](https://example.com/x.png)',             # 外链：不该动
    '没有任何图片的正文',
    '',
]


def test_rewrite_matches_legacy_byte_for_byte():
    """★ 收敛**不改变行为**：每个样本上新旧输出逐字节相同。

    单看「新实现能跑」是不够的——捕获组布局从「组外」换成「组内 + 去前缀」，
    最容易犯的错是子目录路径（`assets/sub/x.png`）多留或少留一段 `assets/`。
    """
    for s in _SAMPLES:
        old = _legacy_store_impl(s, "tr_1")
        new = rewrite_asset_urls(s, "tr_1")
        assert old == new, f"样本 {s!r}：\n  旧 {old!r}\n  新 {new!r}"


def test_control_legacy_impl_differs_when_prefix_not_stripped():
    """对照实验（模式 #38）：证明上面那条**有区分力**。

    把「去掉 assets/ 前缀」这一步拿掉，同一个样本必须产出**不同**结果——
    否则「逐字节等价」这条断言可能只是恒真（两组都恰好对）。
    """
    broken = ASSET_REF_RE.sub(
        lambda m: f"![{m.group(1)}](/api/assets/tr_1/{m.group(2)})",  # 忘了 asset_rel_path
        '![a](assets/x.png)')
    assert broken == '![a](/api/assets/tr_1/assets/x.png)'
    assert broken != rewrite_asset_urls('![a](assets/x.png)', 'tr_1'), \
        "漏掉去前缀竟与正确实现同结果——说明这条样本挑得不好（挑不出差异）"


def test_asset_rel_path():
    """`assets/x` → `x`；不带前缀时原样返回（正则保证不会发生，留着是为了误用时看得见）。"""
    assert asset_rel_path("assets/img_0001.png") == "img_0001.png"
    assert asset_rel_path("assets/sub/img.png") == "sub/img.png"
    assert asset_rel_path("img.png") == "img.png"


# ═══════════════════════════════════════════════════════════════
# ② 正/反成对（模式 #52）
# ═══════════════════════════════════════════════════════════════

def test_rewrite_and_unrewrite_are_inverse():
    """GET 改写 → 编辑保存反转 = 恒等。反向只认**本文档**、不碰 `<img>`。"""
    for s in ('![a](assets/img_0001.png)',
              '前 ![a](assets/a.png) 后 ![b](assets/sub/b.png)'):
        assert unrewrite_asset_urls(rewrite_asset_urls(s, "tr_1"), "tr_1") == s

    # 别的文档的引用、以及用户手写的 <img>，都**不**反转
    foreign = '![a](/api/assets/other/y.png)'
    assert unrewrite_asset_urls(foreign, "tr_1") == foreign
    img = '<img src="/api/assets/tr_1/z.png">'
    assert unrewrite_asset_urls(img, "tr_1") == img


def test_control_unrewrite_has_discrimination():
    """对照实验：反向函数真在做事（不是「原样返回」蒙混过关）。"""
    assert unrewrite_asset_urls('![a](/api/assets/tr_1/x.png)', "tr_1") \
        == '![a](assets/x.png)', "反向没有真的转换——往返断言会因此恒真"


# ═══════════════════════════════════════════════════════════════
# ③ 同一编译对象（`m.re is` 判据依赖身份）
# ═══════════════════════════════════════════════════════════════

def _imports_from_core(rel: str, name: str) -> bool:
    """文件是否从 `app.core.markdown_assets` import 了 `name`。"""
    tree = ast.parse((_BACKEND / rel).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module != "app.core.markdown_assets":
            continue
        if any(a.name == name for a in node.names):
            return True
    return False


def test_all_call_sites_share_one_object():
    """调用点 import 的是**同一个对象**。

    ⚠️ `content_preparer._replacer` 与 `export._collect_image_data` 都写
    `m.re is ASSET_REF_RE`。各自 `re.compile` 同一份字符串会得到两个对象，
    `is` 恒假 → 那个分支永不进入 → 症状是「assets 图片全部原样留在正文里」
    而日志显示剥离数为 0（**不报错**）。故断言的是「import 自同一处」，
    而不是「模式字符串相同」。
    """
    missing = [rel for rel in _IMPORTERS_RE if not _imports_from_core(rel, "ASSET_REF_RE")]
    assert not missing, f"这些调用点没有 import core 的 ASSET_REF_RE：{missing}"

    # store.py 走兼容层：re-export 函数而非正则对象（它不参与 `m.re is` 判据）
    assert _imports_from_core(_STORE_REEXPORT, "rewrite_asset_urls"), \
        "store.py 不再 re-export rewrite_asset_urls——5 个既有调用点会 ImportError"


# ═══════════════════════════════════════════════════════════════
# ④ 零内联（AST 守卫，模式 #54）
# ═══════════════════════════════════════════════════════════════

def _compiled_pattern(node: ast.Call) -> str | None:
    """取 `re.compile(<字面量>)` 的**字符串值**——不是源码文本。

    ⚠️ 不能用 `ast.unparse`：它把 raw string 里的 `\\s` 重新转义成 `\\\\s`，
    于是按「源码文本」写的子串判据永远匹配不上 → 守卫**恒假通过**（模式 #38
    的典型形态：判据跑了，但它看的不是它以为的那个）。拼接形态
    （`re.compile(A + x + B)`）取不到字面量 → 返回 None，与「不含特征」同处理。
    """
    if not node.args:
        return None
    try:
        val = ast.literal_eval(node.args[0])
    except Exception:
        return None
    return val if isinstance(val, str) else None


def _generic_asset_regex_definitions() -> list[str]:
    """全仓找 `re.compile(...)` 且模式里含**通用形态**特征的定义点。"""
    hits: list[str] = []
    for py in sorted(_APP.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if not (isinstance(f, ast.Attribute) and f.attr == "compile"):
                continue
            if _GENERIC_MARK in (_compiled_pattern(node) or ""):
                hits.append(f"{py.relative_to(_BACKEND).as_posix()}:{node.lineno}")
    return hits


def test_generic_asset_regex_defined_exactly_once():
    """通用版 `assets/` 正则只允许一处定义（`core/markdown_assets.py`）。"""
    hits = _generic_asset_regex_definitions()
    assert len(hits) == 1, f"通用版 assets/ 正则应只定义一处，实际：{hits}"
    assert hits[0].startswith("app/core/markdown_assets.py"), hits


def _first_compiled(src: str) -> ast.Call:
    return next(n for n in ast.walk(ast.parse(src))
                if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "compile")


def test_control_generic_detector_has_discrimination():
    """对照实验：守卫能抓到通用形态、且**不**误伤严格版（模式 #38/#54）。

    严格版（`ocr_preprocessor` 的 `img_/fig_` 命名）是**刻意保留**的，不在收敛范围；
    守卫若把它也判成违规，就会逼着下一个人去「统一」一个**不该统一**的东西。
    """
    # ① 真·通用版：必须被抓到（证明守卫**会红**，不是永真通过）
    got = _compiled_pattern(_first_compiled(
        r'p = re.compile(r"!\[([^\]]*)\]\((assets/[^\s)]+)\)")'))
    assert got is not None and _GENERIC_MARK in got, \
        f"守卫抓不到通用形态（raw string 的取值判据写错了）：{got!r}"

    # ② 严格版：必须**不**被抓到（对照 ①，证明判据有区分力而非一律命中）
    strict = _compiled_pattern(_first_compiled(
        r'p = re.compile(r"!\[([^\]]*)\]\((assets/((?:img|fig)_\d{4}\.[a-z]+))\)")'))
    assert strict is not None and _GENERIC_MARK not in strict, \
        "严格版被判成了通用版——守卫会逼人合并一个不该合并的东西"

    # ③ 拼接形态取不到字面量 → None（`unrewrite_asset_urls` 里的 `/api/assets/` 即此类）
    assert _compiled_pattern(_first_compiled(
        'p = re.compile(r"x/api/assets/" + id + r"/([^\\s)]+)")')) is None, \
        "拼接形态不应被取成字面量（否则会误报）"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
