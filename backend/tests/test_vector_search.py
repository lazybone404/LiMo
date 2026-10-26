"""语义检索融合层 —— RRF 排序 / 退化显式 / 端点契约。

运行：cd backend && pytest tests/test_vector_search.py

⚠️ 本文件守的是**三件都不抛错**的事：

  1. **退化必须显式**：向量路挂了（未建索引 / 口径不符 / 库坏 / 编码失败 / 超时）时
     字面结果照样返回，但 `semantic.error` 必须带上原因。缺了它，用户看到的是
     「搜得到，但好像没变聪明」，而无法区分「向量没帮上忙」与「向量压根没跑」。
  2. **退化时一次编码都不发**：`_precheck` 拦在前面。写漏一个分支的形态是
     「每次搜索都白花一次 embedding 的钱」，而结果完全正确 —— 看不出来。
  3. **两路同深度**：RRF 的 `1/(k+rank)` 只在两路基线一致时可比。字面取 20、
     向量取 50 会让「字面第 20 名」与「向量第 20 名」拿到不同分数。

⚠️ 用**真的 `VectorStore`**（临时目录）而不是假的向量库：`load_matrix` 的
「空库 → None」「维度不符 → 跳过」这两条分支只有真库走得到，假件会把它们抹平。
"""
import asyncio
import contextlib
import os
import shutil
import sys
import tempfile
from types import SimpleNamespace

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.core.dependencies import get_query_service, get_vector_searcher
from app.core.errors import AppError
from app.core.vector_store import DEFAULT_CHUNK_SIZE, VectorStore
from app.services.vector_search import RRF_K, VectorSearcher
from main import app

SIZE = DEFAULT_CHUNK_SIZE
DIM = 2


class _QueryEngine:
    """只服务「查询一次编码」这一个动作的假引擎。"""
    name = "fake-embedding"
    model_id = "m"
    dimension = DIM
    capabilities = ["embedding"]

    def __init__(self, vec=(1.0, 0.0), *, delay=0.0, mode=None):
        self.vec = list(vec)
        self.delay = delay
        self.mode = mode                       # None / "boom" / "empty" / "wrong_dim"
        self.calls = 0

    async def embed(self, texts):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.mode == "boom":
            raise RuntimeError("编码失败")
        if self.mode == "empty":
            return []
        if self.mode == "wrong_dim":
            return [[1.0, 0.0, 0.0] for _ in texts]
        return [list(self.vec) for _ in texts]


class _LocalQueryEngine(_QueryEngine):
    """本地引擎的形状：`warmup` 要花几秒把权重读进内存。

    同 `test_vector_worker.py::_LocalEngine` 用子类 —— 云端引擎**没有**这个钩子，
    而 `_ensure_loaded` 的 `warm is None` 分支正是为它存在的（给基类加上就等于
    所有用例都只在有钩子的那条路上跑）。
    """

    def __init__(self, *a, warm_delay=0.0, warm_error=None, **kw):
        super().__init__(*a, **kw)
        self.warm_delay = warm_delay
        self.warm_error = warm_error
        self.warm_calls = 0

    async def warmup(self):
        self.warm_calls += 1
        if self.warm_delay:
            await asyncio.sleep(self.warm_delay)
        if self.warm_error is not None:
            raise self.warm_error


class _DownVec:
    """索引库不可用（文件被删 / 权限坏了）—— 真 `VectorStore` 的 `available=False` 分支。"""
    available = False

    def read_identity(self):
        return None

    def identity_ok(self, *a, **k):
        return False

    def load_matrix(self):
        return None


@contextlib.contextmanager
def _vec(**kw):
    d = tempfile.mkdtemp(prefix="limo_vec_search_")
    try:
        vs = VectorStore(d)
        assert vs.available, "临时库应可用（否则后面测的都不是想测的那条）"
        if kw.pop("identity", True):
            assert vs.ensure_identity("m", DIM, SIZE)
        yield vs
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _put(vs: VectorStore, tx_id: str, vec) -> None:
    vs.replace_chunks(tx_id, [{"idx": 0, "text": tx_id, "vec": vec}])


def _corpus(vs: VectorStore) -> None:
    """四篇，与查询 `[1, 0]` 的余弦分别是 1.0 / 0.8 / 0.6 / 0.0。"""
    _put(vs, "a", [1.0, 0.0])
    _put(vs, "c", [0.8, 0.6])
    _put(vs, "x", [0.6, 0.8])
    _put(vs, "b", [0.0, 1.0])


def _fuse(vs, engine, lexical, *, limit, depth=None, chunk_size=SIZE,
          timeout=None, load_timeout=None):
    kw: dict = {}
    if timeout is not None:
        kw["timeout"] = timeout
    if load_timeout is not None:
        kw["load_timeout"] = load_timeout
    searcher = VectorSearcher(vs, engine, chunk_size=chunk_size, **kw)
    return asyncio.run(searcher.fuse("q", lexical, limit=limit, depth=depth))


# ── 融合排序 ──

def test_融合把语义靠前的文档提上来():
    """字面第一名掉到第三、字面第三名升到第一 —— RRF 真的在改顺序。

    数值可手算（k=10）：a = 1/13 + 1/11、c = 1/12 + 1/12、b = 1/11 + 1/14。
    """
    with _vec() as vs:
        _corpus(vs)
        ids, sem = _fuse(vs, _QueryEngine(), ["b", "c", "a"], limit=3)
    assert ids == ["a", "c", "b"], f"融合顺序不对：{ids}"
    assert sem == {"error": None, "hits": 4}, sem


def test_语义独有命中出现在结果里且只出现一次():
    """`x` 一个词都没在字面结果里，靠语义路进来；`a` 两路都有，不能重复。"""
    with _vec() as vs:
        _corpus(vs)
        ids, _ = _fuse(vs, _QueryEngine(), ["b", "c", "a"], limit=10)
    assert ids == ["a", "c", "b", "x"], ids
    assert len(ids) == len(set(ids))


def test_depth_限制向量路召回数():
    """depth 是**向量路**的取样深度（字面的深度由调用方决定，见 router 的 max()）。"""
    with _vec() as vs:
        _corpus(vs)
        ids, sem = _fuse(vs, _QueryEngine(), ["b", "c", "a"], limit=10, depth=2)
    assert sem["hits"] == 2 and "x" not in ids, (sem, ids)


def test_limit_截在融合之后():
    with _vec() as vs:
        _corpus(vs)
        ids, _ = _fuse(vs, _QueryEngine(), ["b", "c", "a"], limit=2)
    assert ids == ["a", "c"], ids


def test_rrf_k_取常量():
    """融合用的是模块常量，不是内联字面量（改 k 时只需改一处）。"""
    assert RRF_K == 10


# ── 退化：字面照给 + error 文案 + 零编码 ──

def test_未建索引_退化为字面且不发编码请求():
    with _vec(identity=False) as vs:
        engine = _QueryEngine()
        ids, sem = _fuse(vs, engine, ["b", "c", "a"], limit=2)
    assert ids == ["b", "c"], "字面结果必须原样返回（截到 limit）"
    assert "尚未建立" in sem["error"] and sem["hits"] == 0, sem
    assert engine.calls == 0, "未建索引时绝不能发编码请求（那是白花钱）"


def test_口径不符_退化为字面且不发编码请求():
    with _vec() as vs:
        _corpus(vs)
        # 库里记的是别的模型 → 这批向量与当前引擎的解释空间不是同一个
        asyncio.run(_reset(vs, "other-model"))
        engine = _QueryEngine()
        ids, sem = _fuse(vs, engine, ["b", "c", "a"], limit=2)
    assert ids == ["b", "c"]
    assert "口径" in sem["error"] and "重建" in sem["error"], sem
    assert engine.calls == 0


async def _reset(vs, model_id):
    vs.reset_identity(model_id, DIM, SIZE)


def test_库不可用_退化为字面且不发编码请求():
    engine = _QueryEngine()
    ids, sem = _fuse(_DownVec(), engine, ["b", "a"], limit=2)
    assert ids == ["b", "a"]
    assert "不可用" in sem["error"], sem
    assert engine.calls == 0


def test_索引为空_不报错也不伪造命中():
    """口径已初始化但一块都没有（刚开开关、worker 还没跑）：
    `error` 必须是 None —— 这不是失败；`hits=0` 是「开了但还没有可比向量」
    与「压根没开（semantic 为 null）」的唯一区分点。"""
    with _vec() as vs:
        engine = _QueryEngine()
        ids, sem = _fuse(vs, engine, ["b", "c", "a"], limit=2)
    assert ids == ["b", "c"]
    assert sem == {"error": None, "hits": 0}, sem
    assert engine.calls == 1, "空库仍然编码了一次查询（否则分不清是没跑还是空库）"


def test_编码超时_退化为字面():
    with _vec() as vs:
        _corpus(vs)
        searcher = VectorSearcher(vs, _QueryEngine(delay=0.2), chunk_size=SIZE, timeout=0.01)
        ids, sem = asyncio.run(searcher.fuse("q", ["b", "c"], limit=2))
    assert ids == ["b", "c"]
    assert "超时" in sem["error"], sem


def test_编码失败_退化为字面():
    with _vec() as vs:
        _corpus(vs)
        ids, sem = _fuse(vs, _QueryEngine(mode="boom"), ["b", "c"], limit=2)
    assert ids == ["b", "c"]
    assert "暂不可用" in sem["error"], sem


def test_编码契约违约_退化为字面():
    """条数不符 / 维度不符都算违约 —— 静默用错维度的向量做点积会得到垃圾排名，
    而 `mat @ qv` 在维度对不上时会直接抛 `ValueError`（那会变成 500）。"""
    for mode in ("empty", "wrong_dim"):
        with _vec() as vs:
            _corpus(vs)
            ids, sem = _fuse(vs, _QueryEngine(mode=mode), ["b", "c"], limit=2)
        assert ids == ["b", "c"], mode
        assert sem["error"], f"{mode} 必须显式退化，实际 {sem}"


# ── 模型载入（本地引擎被 `vector_worker.maybe_release` 放掉之后） ──

def test_查询前先把模型载入():
    """模型被释放后，查询侧必须**先载入再编码**（`_ensure_loaded`）。

    漏了这条的失效形态：`embed()` 自己惰性加载 3–5 秒，全部耗在 `QUERY_TIMEOUT`
    里 ⇒ **每次冷查询都必然超时**并退回纯字面，而提示写着「语义检索超时」，
    读起来像网络问题（可本地模型根本不联网）。
    """
    with _vec() as vs:
        _corpus(vs)
        eng = _LocalQueryEngine()
        ids, sem = _fuse(vs, eng, ["b", "c", "a"], limit=3)
    assert sem == {"error": None, "hits": 4}, sem
    assert ids[0] == "a"
    assert eng.warm_calls == 1 and eng.calls == 1, (eng.warm_calls, eng.calls)


def test_载入失败只降级这一次查询():
    """载入失败 ≠ 索引坏了：字面照给，文案点明是**载入**。"""
    with _vec() as vs:
        _corpus(vs)
        eng = _LocalQueryEngine(warm_error=RuntimeError("模型文件不见了"))
        ids, sem = _fuse(vs, eng, ["b", "c", "a"], limit=2)
    assert ids == ["b", "c"]
    assert "载入失败" in sem["error"], sem
    assert "RuntimeError" in sem["error"], "原因要带上异常类型，否则排查只能靠猜"
    assert eng.calls == 0, "载入失败时绝不能发编码请求（引擎内部会再加载一次）"


def test_载入超时是独立的一档():
    """载入超出 `load_timeout` → 单独一句「载入超时」，不与「检索超时」混为一谈。"""
    with _vec() as vs:
        _corpus(vs)
        eng = _LocalQueryEngine(warm_delay=0.3)
        ids, sem = _fuse(vs, eng, ["b", "c"], limit=2, load_timeout=0.05)
    assert ids == ["b", "c"]
    assert "载入超时" in sem["error"], sem
    assert eng.calls == 0


def test_载入耗时不计入查询超时():
    """⚠️ 两个超时是**两个常量**：载入用 `load_timeout`，编码用 `timeout`。

    这里把 `timeout` 压到 0.01 而让载入花 0.2 秒 —— 若两者共用一个时钟，载入
    就会被 0.01 打断、报成「载入超时」（那正是这条要区分的两句话）；分开后载入
    顺利通过，超时发生在**编码**那一步。
    """
    with _vec() as vs:
        _corpus(vs)
        # 载入与编码各慢 0.2s，查询超时 0.01s：只有「编码那一步」该被判超时
        eng = _LocalQueryEngine(warm_delay=0.2, delay=0.2)
        ids, sem = _fuse(vs, eng, ["b", "c"], limit=2, timeout=0.01, load_timeout=30)
    assert ids == ["b", "c"]
    assert "载入" not in sem["error"], f"载入不该被查询超时打断：{sem}"
    assert "超时" in sem["error"], sem
    assert eng.warm_calls == 1


# ── 四元组比对：查询侧只读 ──

def test_identity_ok_只读_不建库():
    """查询路径绝不能用 `ensure_identity` —— 它首次调用会**写库**（那就是初始化）。"""
    with _vec(identity=False) as vs:
        assert vs.read_identity() is None
        assert vs.identity_ok("m", DIM, SIZE) is False
        assert vs.read_identity() is None, "identity_ok 不得写库"


def test_identity_ok_四元组任一不符即否():
    with _vec() as vs:
        assert vs.identity_ok("m", DIM, SIZE) is True
        assert vs.identity_ok("other", DIM, SIZE) is False
        assert vs.identity_ok("m", DIM + 1, SIZE) is False
        assert vs.identity_ok("m", DIM, SIZE + 1) is False


# ── HTTP 端点契约 ──

class _FakeSvc:
    def __init__(self, hits, titles=None):
        self._hits = hits
        self._titles = titles or {}
        self.calls: list[tuple[str, int]] = []

    def search_resources(self, q, *, limit=20, cursor=None):
        self.calls.append((q, limit))
        return SimpleNamespace(items=self._hits)

    def get_resource(self, doc_id):
        if doc_id not in self._titles:
            raise AppError("QUERY_NOT_FOUND", "没有这篇文档", status_code=404)
        return {"id": doc_id, "title": self._titles[doc_id]}


class _FakeSearcher:
    def __init__(self, ids, semantic=None):
        self._ids = list(ids)
        self._sem = semantic if semantic is not None else {"error": None, "hits": len(ids)}
        self.args = None

    async def fuse(self, query, lexical_ids, *, limit, depth=None):
        self.args = (query, list(lexical_ids), limit, depth)
        return self._ids[:limit], self._sem


@contextlib.contextmanager
def _http(svc, searcher):
    app.dependency_overrides[get_query_service] = lambda: svc
    app.dependency_overrides[get_vector_searcher] = lambda: searcher
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_query_service, None)
        app.dependency_overrides.pop(get_vector_searcher, None)


_LEX = [
    {"id": "b", "title": "B", "snippet": "…b…"},
    {"id": "c", "title": "C", "snippet": ""},
    {"id": "a", "title": "A", "snippet": "…a…"},
]


def test_limit_100_不返回_422():
    """⚠️ 回归守卫：后端曾写 `le=50` 而前端固定要 100 条 ⇒ 文档库搜索框
    **每次都是 422、一条结果都没有**。三条测试路径全都绕过了它（前端 mock 掉
    `api/search`、`test_search.py` 测 SearchEngine 类、评测脚本直调 `engine.search()`），
    所以这条必须走真 HTTP 链路。"""
    with _http(_FakeSvc(_LEX), None) as c:
        r = c.get("/api/search", params={"q": "x", "limit": 100})
        assert r.status_code == 200, r.text
        assert len(r.json()["hits"]) == 3
        assert c.get("/api/search", params={"q": "x", "limit": 101}).status_code == 422


def test_未启用时_semantic_为_null():
    with _http(_FakeSvc(_LEX), None) as c:
        body = c.get("/api/search", params={"q": "x", "limit": 2}).json()
    assert body["semantic"] is None, "未启用与「启用了但没命中」必须能区分"
    assert [h["id"] for h in body["hits"]] == ["b", "c"]


def test_字面路按_depth_取样而不是_limit():
    """`depth = max(limit, DEFAULT_DEPTH)`：两路必须同样深，否则 RRF 的
    `1/(k+rank)` 比的不是同一件事。"""
    svc = _FakeSvc(_LEX)
    with _http(svc, _FakeSearcher(["a", "b", "c"])) as c:
        c.get("/api/search", params={"q": "x", "limit": 3})
    assert svc.calls == [("x", 50)], svc.calls


def test_语义独有命中回补标题且_snippet_为空():
    """向量能召回一个词都没出现的文档 —— 它在字面结果里没有条目可继承。
    `snippet` 留空而不是省键：语义命中没有可截取的高亮位置，硬造摘要
    等于凭空发明「为什么匹配」。"""
    svc = _FakeSvc(_LEX, titles={"x": "语义独有"})
    with _http(svc, _FakeSearcher(["a", "x", "b"])) as c:
        body = c.get("/api/search", params={"q": "x", "limit": 3}).json()
    assert [h["id"] for h in body["hits"]] == ["a", "x", "b"]
    assert body["hits"][1] == {"id": "x", "title": "语义独有", "snippet": ""}


def test_孤儿命中被丢掉而不是_500():
    """向量库里有、文档表里没有（用户刚删了那篇，对账还没跑到）——
    不能让一个已删除的文档毁掉整次搜索。"""
    svc = _FakeSvc(_LEX, titles={})
    with _http(svc, _FakeSearcher(["a", "gone"])) as c:
        r = c.get("/api/search", params={"q": "x", "limit": 3})
    assert r.status_code == 200
    assert [h["id"] for h in r.json()["hits"]] == ["a"]


def test_semantic_错误随响应返回():
    sem = {"error": "向量索引库不可用", "hits": 0}
    with _http(_FakeSvc(_LEX), _FakeSearcher(["b"], sem)) as c:
        body = c.get("/api/search", params={"q": "x"}).json()
    assert body["semantic"] == sem, "退化原因必须原样到前端（否则只剩「好像没变聪明」）"
    assert [h["id"] for h in body["hits"]] == ["b"]
