"""引擎类别清单的单一真相源（P2-22）。

运行：./.venv/Scripts/python.exe -m pytest tests/test_engine_categories.py -q

**修复的是什么**：`asr / ocr / doc` 这组类别曾在**三处**各写一份 ——
`routers/capabilities.py` 两处（过滤用的 set + `/engine-registry` 遍历用的 list）
与 `core/config.py` 一处（过滤用的 set）。其中一处的形态（list）**已经与另两处分叉**，
所以「三处一致」这句话在当时就已经不成立。

⚠️ 两个视图（set / tuple）**刻意并存，不能合并**：过滤只问「在不在」，
遍历则要求**顺序**（`/engine-registry` 的分组顺序 = 前端引擎选择器的分组顺序）。
合并成一个就得在遍历处补 `sorted()`，那是把「刻意的业务顺序」换成「字典序」——
一次没人会注意到的界面顺序变更。

本文件守四件事：
  ① 两个视图内容一致（去重后集合相等）
  ② 全仓不再有第二份类别字面量（AST 守卫，模式 #54）
  ③ `/engine-registry` 的**响应 key 顺序**确实来自 ORDER 视图
  ④ `core/registry.py` 不反向依赖 config（新建的 config → registry 边不能变成环）
"""

import ast
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.core.registry import ENGINE_CATEGORIES, ENGINE_CATEGORY_ORDER
from app.routers.capabilities import engine_registry

_BACKEND = Path(__file__).resolve().parent.parent
_APP = _BACKEND / "app"

# 刻意的「第二双眼睛」：类别清单改了这里必须跟着改。
# ⚠️ `embedding`（2026-10-26）与前三类性质不同（吃文本吐向量，不作用于文件），
#    但它仍是**同一张表里的一个类别**——发现机制一视同仁，另建一张表才是模式 #54。
_CATS = ("asr", "ocr", "doc", "embedding")


# ═══════════════════════════════════════════════════════════════
# ① 两个视图内容一致
# ═══════════════════════════════════════════════════════════════

def test_two_views_agree():
    """去重后的集合相等，且 ORDER 自己无重复。"""
    assert set(ENGINE_CATEGORY_ORDER) == set(ENGINE_CATEGORIES)
    assert len(ENGINE_CATEGORY_ORDER) == len(set(ENGINE_CATEGORY_ORDER)), \
        "ORDER 里有重复项——遍历时会把同一类别输出两次"
    assert ENGINE_CATEGORIES == frozenset(_CATS), \
        "类别集合变了？那就一并更新本文件的 _CATS（它是刻意的第二双眼睛）"


def test_views_are_immutable():
    """两个视图都是不可变容器——共享常量被就地改掉是最难查的一类事故。"""
    assert isinstance(ENGINE_CATEGORIES, frozenset)
    assert isinstance(ENGINE_CATEGORY_ORDER, tuple)


# ═══════════════════════════════════════════════════════════════
# ② 全仓零内联（AST 守卫，模式 #54）
# ═══════════════════════════════════════════════════════════════

def _inline_category_literals() -> list[str]:
    """找「`asr` 与 `ocr` 与 `doc` 同时出现在一个字面量容器里」的赋值点。

    ⚠️ 走 AST 而非 grep：`registry.py` 与各测试的**注释里**会引用旧写法当反例，
    grep 会把「讲代码的文字」判成违规（模式 #54 的配套细节）。
    """
    hits: list[str] = []
    for py in sorted(_APP.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Set, ast.List, ast.Tuple)):
                continue
            vals = {e.value for e in node.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)}
            if vals == set(_CATS):
                hits.append(f"{py.relative_to(_BACKEND).as_posix()}:{node.lineno}")
    return hits


def test_category_literal_defined_exactly_once():
    """类别字面量只允许出现在 `core/registry.py`（两处：set 视图 + tuple 视图）。"""
    hits = _inline_category_literals()
    where = {h.split(":")[0] for h in hits}
    assert where == {"app/core/registry.py"}, \
        f"类别字面量只该定义在 core/registry.py，实际：{hits}"
    assert len(hits) == 2, f"registry.py 里应恰有 2 处（两个视图），实际：{hits}"


def test_control_detector_has_discrimination():
    """对照实验：守卫能抓到内联形态，且不误伤注释与「部分匹配」（模式 #38）。

    ⚠️ 样例串**从 `_CATS` 现拼**，不手写 `{"asr", "ocr", "doc"}` ——
    手写的那份在本轮加 `embedding` 时当场失效（样例不再等于完整清单，于是
    「守卫抓不到最简单的内联形态」为真而红）。对照实验的输入必须跟着被测清单走，
    否则它守的是**过去的**那一版清单。
    """
    full = "{" + ", ".join(f'"{c}"' for c in _CATS) + "}"
    inline = ast.parse(f"X = {full}")
    found = [n for n in ast.walk(inline) if isinstance(n, ast.Set)
             and {e.value for e in n.elts} == set(_CATS)]
    assert len(found) == 1, f"守卫抓不到最简单的内联形态：{full}"

    # 少一个类别 → **不该**被当成完整清单（否则将来加类别时会被误判为重复定义）
    fewer = ", ".join(f'"{c}"' for c in _CATS[:-1])
    partial = ast.parse(f"X = {{{fewer}}}")
    assert not [n for n in ast.walk(partial) if isinstance(n, ast.Set)
                and {e.value for e in n.elts} == set(_CATS)]

    # 纯注释里的旧写法根本不进 AST
    assert not [n for n in ast.walk(ast.parse('# X = {"asr", "ocr", "doc"}\n'))
                if isinstance(n, ast.Set)]


# ═══════════════════════════════════════════════════════════════
# ③ 响应顺序来自 ORDER 视图
# ═══════════════════════════════════════════════════════════════

def test_engine_registry_endpoint_order():
    """`/engine-registry` 的 key 顺序 == ORDER 视图的顺序。

    ⚠️ 这条测的是**顺序**而不是集合：把实现改成 `sorted(ENGINE_CATEGORIES)`
    会得到一个集合相同、顺序不同的响应，而前端按响应顺序渲染分组——
    症状只是「设置页引擎分组的次序变了」，没有断言就永远抓不到。
    """
    result = asyncio.run(engine_registry())
    assert list(result) == list(ENGINE_CATEGORY_ORDER), \
        f"响应顺序应等于 ENGINE_CATEGORY_ORDER：{list(result)}"
    assert set(result) == set(ENGINE_CATEGORIES)


# ═══════════════════════════════════════════════════════════════
# ④ registry 不反向依赖 config（防新建的边变成环）
# ═══════════════════════════════════════════════════════════════

def test_registry_does_not_import_config():
    """`core/config.py` 现在 import `core/registry` —— 反向那一条边会直接成环。

    ⚠️ 环的症状是**启动期**的 `ImportError`（通常是 partially initialized module），
    而它只在 import 顺序恰好踩中时出现 —— 属于「本地跑得好好的、打包后起不来」。
    """
    tree = ast.parse((_APP / "core" / "registry.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    offenders = [m for m in imported if m.split(".")[-1] in ("config", "config_io")]
    assert not offenders, f"core/registry.py 不该 import config（会与 config → registry 成环）：{offenders}"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
