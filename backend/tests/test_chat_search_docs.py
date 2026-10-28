"""对话侧检索 —— `search_docs` 工具必须**走真检索**，且说真话。

改造前它是 `store.list_transcripts({"query": q})`，即**标题子串过滤**：正文里逐字
存在的词只要标题没有就搜不到。而界面上那个搜索框走的是 FTS5 ∪ 向量 —— 于是
「AI 说库里没有，可我用搜索框自己搜得到」，两边都不报错，用户只能靠对比结果察觉。

本文件钉四件事，每件都对应一种「改回去也不会红」的静默失效：

1. **接线**：`search_docs` 真的调 `fused_search`（不是又写回标题过滤）。
2. **async**：`AgentRunner` 靠 `asyncio.iscoroutinefunction` 分派，写成同步函数
   会让「融合检索」这条路根本无法 await（改造前是同步的，改的时候最容易忘）。
3. **`found` 不等于命中数**：`found` 是「返回了几条」（上限 5），`hasMore` 才是
   「还有更多」。不拆开时「命中 200 篇」会说成「找到 5 个」—— 那是说假话。
4. **降级可观测**：索引坏时要**打上 `degraded` 再回退**，而不是抛错（抛错会被
   `agent.py` 吞成 `{"error":...}`，等于索引坏一次对话从此一篇都搜不到），
   也不是静默降级（模型会把「没搜到」当事实）。
"""
import asyncio
import inspect
import json
from types import SimpleNamespace

import pytest

from app.core.errors import AppError
from app.routers.chat import (
    SEARCH_DOCS_LIMIT,
    _build_registry,
    _make_search_docs,
    chat_stream,
)

# ═══════════════════════════════════════════════════════════
# 测试替身
# ═══════════════════════════════════════════════════════════


class _Svc:
    """`QueryService` 的最小子集 —— 只提供 `fused_search` 会用到的那两个方法。

    ⚠️ 不 mock `fused_search` 本身：那样测的就只是「我调了我自己写的桩」，
    产品实现里的 `depth` 计算、`limit` 截断一个都不会被覆盖。
    """

    def __init__(self, n: int = 5, *, error: Exception | None = None):
        self._n = n
        self._error = error
        self.calls: list[tuple[str, int]] = []

    def search_resources(self, q, *, limit=20, cursor=None):
        self.calls.append((q, limit))
        if self._error:
            raise self._error
        items = [
            {"id": f"doc{i}", "title": f"文档 {i}", "snippet": f"正文里有 {q}"}
            for i in range(self._n)
        ]
        # ⚠️ 这里必须像真实现那样**按 limit 截断** —— 替身多返回几条，
        #    `hasMore` 的判据会在测试里恒为真而线上恒为假。
        return SimpleNamespace(items=items[:limit])

    def get_resource(self, doc_id):
        return {"id": doc_id, "title": f"文档 {doc_id}"}


class _Store:
    """`Store` 的最小子集 —— 降级路径走 `list_transcripts({"query": ...})`。"""

    def __init__(self, rows: list[dict] | None = None):
        self.rows = rows or []
        self.queries: list = []

    def list_transcripts(self, filters=None):
        if filters:
            self.queries.append(filters)
        return self.rows


def _run(fn, **kwargs):
    return json.loads(asyncio.run(fn(**kwargs)))


# ═══════════════════════════════════════════════════════════
# 1. 接线 + async
# ═══════════════════════════════════════════════════════════


def test_search_docs_是协程():
    """`AgentRunner` 靠 `asyncio.iscoroutinefunction` 分派（`llm/agent.py`）。

    写成同步函数时，`fused_search` 的 `await` 根本无处可放 —— 这条断言是那个
    分派前提在测试里的落点，不是风格检查。
    """
    fn = _make_search_docs(_Store(), _Svc())
    assert asyncio.iscoroutinefunction(fn)


def test_search_docs_走融合检索而不是标题过滤():
    """接线守卫：拿到真检索的结果，且没有碰 `list_transcripts`。"""
    svc, store = _Svc(n=3), _Store([{"id": "x", "title": "不该被用到"}])
    out = _run(_make_search_docs(store, svc), query="并发")

    assert svc.calls, "没有调用 QueryService —— search_docs 又退回标题过滤了"
    assert store.queries == [], "既走检索又走标题过滤，两条路同时在跑"
    assert [r["id"] for r in out["results"]] == ["doc0", "doc1", "doc2"]
    assert out["results"][0]["snippet"], "正文摘要没带给模型，模型仍只能凭标题猜内容"


def test_两个对话端点都把依赖传给了工具表():
    """接线守卫（路由层）。

    ⚠️ `chat_stream` 是嵌套 async generator，测试够不到内层表达式 —— 少了这条，
    把 `svc=svc` 从 `_build_registry(...)` 里删掉时**上面所有用例照样全绿**，
    而线上 `search_docs` 永远走降级路径。
    """
    for fn in (chat_stream,):
        src = inspect.getsource(fn)
        assert "svc=svc" in src and "searcher=searcher" in src, (
            f"{fn.__name__} 没有把检索依赖传给 _build_registry")


# ═══════════════════════════════════════════════════════════
# 2. found 与 hasMore
# ═══════════════════════════════════════════════════════════


def test_命中不足上限时_hasMore为假():
    out = _run(_make_search_docs(_Store(), _Svc(n=2)), query="q")
    assert out["found"] == 2 and out["hasMore"] is False


def test_命中恰好等于上限时_hasMore为假():
    """边界：多要的那一条**不能**把「刚好 5 条」误报成「还有更多」。"""
    out = _run(_make_search_docs(_Store(), _Svc(n=SEARCH_DOCS_LIMIT)), query="q")
    assert out["found"] == SEARCH_DOCS_LIMIT and out["hasMore"] is False


def test_命中超过上限时_hasMore为真且只给5条():
    out = _run(_make_search_docs(_Store(), _Svc(n=200)), query="q")
    assert out["found"] == SEARCH_DOCS_LIMIT
    assert len(out["results"]) == SEARCH_DOCS_LIMIT
    assert out["hasMore"] is True, (
        "命中远超上限却说「就这些」—— _tool_summary 会把 200 篇说成「找到 5 个」")


def test_向检索要多一条():
    """`hasMore` 的判据是「多要的那条填上了」，所以请求数必须比展示数多 1。"""
    svc = _Svc(n=3)
    _run(_make_search_docs(_Store(), svc), query="q")
    assert svc.calls[0][1] >= SEARCH_DOCS_LIMIT + 1


# ═══════════════════════════════════════════════════════════
# 3. 降级
# ═══════════════════════════════════════════════════════════


def test_索引不可用时降级为标题过滤并标明降级():
    """⚠️ 抛错会让 `agent.py` 把它吞成 `{"error":...}` —— 索引坏一次，对话从此搜不到。"""
    svc = _Svc(error=AppError("SEARCH_UNAVAILABLE", "索引不可用"))
    store = _Store([{"id": "a", "title": "标题里有"}, {"id": "b", "title": "另一篇"}])
    out = _run(_make_search_docs(store, svc), query="词")

    assert out["degraded"] == "index_unavailable"
    assert [r["id"] for r in out["results"]] == ["a", "b"]
    assert store.queries, "降级路径没有真正回退到标题过滤"


def test_没有检索依赖时同样标明降级():
    """`_build_registry(None)` 这类构造（既有测试在用）不该静默退化成「检索过了」。"""
    store = _Store([{"id": "a", "title": "t"}])
    out = _run(_make_search_docs(store), query="q")
    assert out["degraded"] == "no_query_service"


def test_正常路径不带降级标记():
    """对照：`degraded` 恒为 None 时上面两条就失去了区分力。"""
    out = _run(_make_search_docs(_Store(), _Svc()), query="q")
    assert out["degraded"] is None


# ═══════════════════════════════════════════════════════════
# 4. 工具表里挂的就是它
# ═══════════════════════════════════════════════════════════


def test_注册表里的_search_docs_带着检索依赖():
    reg = _build_registry(_Store(), svc=_Svc(n=1))
    fn = reg.get_fn("search_docs")
    assert asyncio.iscoroutinefunction(fn)
    out = _run(fn, query="q")
    assert out["results"][0]["id"] == "doc0"


@pytest.mark.parametrize("bad", ["", "   "])
def test_空查询不炸(bad):
    """模型偶尔会传空 query。空串在 `search_resources` 里返回空 `Page`（真实现），
    替身这里直接给假数据 —— 断言的是**工具层**不会因为空串抛异常。"""
    out = _run(_make_search_docs(_Store(), _Svc(n=0)), query=bad)
    assert out["found"] == 0 and out["results"] == []
