"""候选正文的取数口 —— `bodies()` 拿的是**原文**，且失败只退化成 `{}`。

这条口子是给重排用的：它要 50 篇文档的正文，而 `search_resources` 返回的
`Page.items` 里只有 `{id, title, snippet}`。三条纪律：

1. **取 `body` 列不取 `toks` 列**。后者是二字组串（`机器 器学 学习`），
   喂给重排模型等于喂噪音 —— 而它不报错，只是分数变得没意义。
2. **失败返回 `{}` 而不是抛**。与 `search_resources` 的处置**刻意不同**：
   那边索引坏了要让用户看见（503 + 重建入口），这边少的只是「重排的输入料」，
   抛出去等于一次可选增强的准备工作毁掉整次搜索。
3. **不做成 `search_resources(include_body=True)`**。省下的只是一次
   `IN` 查询，换来的是 `Page` 形状随参数变 + 正文经 HTTP 层泄漏的风险点。
   第 3 条用 AST 守卫钉住 —— 它防的是「下次顺手加上去」。
"""
import shutil
import tempfile

from app.core.search import SearchEngine
from app.services.query import QueryService


def _engine():
    d = tempfile.mkdtemp(prefix="limo_bodies_test_")
    return SearchEngine(d), d


class _Store:
    pass


# ═══════════════════════════════════════════════════════════
# 1. 取的是原文
# ═══════════════════════════════════════════════════════════

def test_批量取回原文而不是二字组串():
    eng, d = _engine()
    try:
        eng.index("a", "标题甲", "本文介绍梯度下降与反向传播的基本原理。")
        eng.index("b", "标题乙", "另一篇讲注意力机制的内容。")

        got = eng.bodies(["a", "b"])

        assert set(got) == {"a", "b"}
        # ⚠️ 判据是「汉字之间没有空格」而不是「不含某个二字组」：后者会误伤，
        # 「梯度下降」本身就含子串「度下」。
        assert "梯度下降" in got["a"], f"拿到的不是原文：{got['a']!r}"
        import re
        assert not re.search(r"[一-鿿]\s+[一-鿿]", got["a"]), (
            f"这是二字组串（`toks` 列）不是原文：{got['a']!r}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_只取要求的那几篇():
    eng, d = _engine()
    try:
        for i in range(5):
            eng.index(f"d{i}", f"标题 {i}", f"正文 {i}")
        assert set(eng.bodies(["d1", "d3"])) == {"d1", "d3"}
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_不存在的_id_不出现在结果里():
    """调用方按 `dict.get` 走兜底链 —— 所以缺键是有意义的信息，不能填 `""`。"""
    eng, d = _engine()
    try:
        eng.index("a", "标题", "正文")
        got = eng.bodies(["a", "不存在"])
        assert set(got) == {"a"}
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ═══════════════════════════════════════════════════════════
# 2. 失败语义：`{}` 而不是抛
# ═══════════════════════════════════════════════════════════

def test_空_id_列表返回空字典():
    eng, d = _engine()
    try:
        assert eng.bodies([]) == {}
        assert eng.bodies(["", "", None]) == {}
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_索引不可用时返回空字典而不是抛():
    """⚠️ 与 `search_resources` **刻意不同**：那边索引坏了要 503 + 重建入口。

    这里是「重排拿不到料」—— 该退化成「这一次不重排」，而不是让用户搜不到东西。
    """
    eng, d = _engine()
    try:
        eng.index("a", "标题", "正文")
        eng.note_error("FTS5 损坏")

        assert eng.available is False
        assert eng.bodies(["a"]) == {}
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_查库抛错时返回空字典():
    """sqlite 层面坏掉（文件被删 / 被锁）同样只留痕不抛。"""
    eng, d = _engine()
    try:
        eng.index("a", "标题", "正文")
        import sqlite3

        def _boom():
            raise sqlite3.OperationalError("database is locked")

        eng._conn = _boom          # 私有方法，但这条测的就是「连接炸了怎么办」
        assert eng.bodies(["a"]) == {}
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ═══════════════════════════════════════════════════════════
# 3. QueryService 的薄转发
# ═══════════════════════════════════════════════════════════

def test_query_service_转发给索引层():
    eng, d = _engine()
    try:
        eng.index("a", "标题", "正文内容")
        svc = QueryService(_Store(), eng)
        assert svc.bodies(["a"]) == {"a": "正文内容"}
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_query_service_没有索引时返回空字典():
    """`main.py` 先建 Store 后建 SearchEngine —— 中间那一瞬间的实例是正常的。"""
    svc = QueryService(_Store(), None)
    assert svc.bodies(["a"]) == {}


def test_query_service_空_id_直接短路():
    """不碰索引层：一次注定空手而归的查询不值得开连接。"""
    class _Spy:
        def __init__(self):
            self.calls = 0

        def bodies(self, ids):
            self.calls += 1
            return {}

    spy = _Spy()
    svc = QueryService(_Store(), spy)
    assert svc.bodies([]) == {}
    assert svc.bodies(["", None]) == {}
    assert spy.calls == 0


# ═══════════════════════════════════════════════════════════
# 4. 守卫：不许把正文塞进 search_resources 的返回值
# ═══════════════════════════════════════════════════════════

def test_search_resources_不得长出让正文搭车的参数():
    """AST 守卫。

    `Page.items` 是 HTTP 层的响应形状（`/api/search` 直接返回它）。加一个
    `include_body=True` 之后，正文会顺着 `search_resources` → router → 前端
    这条**已经被所有人信任的路**流出去，而每一层都不觉得自己在传敏感内容。
    兜底链多一次 50 个 id 的 `IN` 查询（<1ms）比这个便宜得多。
    """
    import ast
    import inspect

    fn = QueryService.search_resources
    params = set(inspect.signature(fn).parameters)
    assert "include_body" not in params
    assert "with_body" not in params and "bodies" not in params

    # 源码里也不许出现这个念头（注释里可以讨论「为什么不这么做」，所以只查
    # **参数/关键字实参**，不查任意子串）
    tree = ast.parse(inspect.getsource(fn).lstrip())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                assert kw.arg not in ("include_body", "with_body"), (
                    f"search_resources 里出现了 {kw.arg}= 实参")
