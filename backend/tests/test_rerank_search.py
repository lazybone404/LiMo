"""重排编排层 —— 失败时**必须退回融合原序**，且失败要能被看见。

`RerankSearcher.rerank` 返回的是**下标顺序**（`list[int]`），不是重排好的 hits：
排序算子只住在这里（同 `core/vector_index.rrf` 的分线），调用方按 order 重排。
于是这个文件测的是「顺序对不对」+「报告字段对不对」，两条线互不掩盖。

核心纪律：**重排是锦上添花，失败不能让人搜不到东西**。超时 / 抛错 / 模型没载入
一律「原序 + 一句人能看懂的原因」，绝不向上抛 —— 一次重排失败毁掉整次检索，
用户看到的是「搜索坏了」，而实际只是排序那一步没成。
"""
import asyncio

import pytest

from app.services.rerank_search import RERANK_TIMEOUT, RerankSearcher

DOCS = ["d0", "d1", "d2", "d3"]


class FakeEngine:
    """可控的重排引擎替身。默认行为 = 按输入顺序原样返回（identity）。"""

    name = "fake-rerank"
    model_id = "fake-model"
    max_documents = 500
    is_local = True

    def __init__(self, order=None, *, delay=0.0, raises=None,
                 precheck_reason=None, warmup_raises=None, max_documents=500):
        self.max_documents = max_documents
        self.calls: list[tuple[str, list[str]]] = []
        self.warm_calls = 0
        self._order = order
        self._delay = delay
        self._raises = raises
        self._precheck_reason = precheck_reason
        self._warmup_raises = warmup_raises

    def precheck(self):
        return self._precheck_reason

    async def warmup(self):
        self.warm_calls += 1
        if self._warmup_raises:
            raise self._warmup_raises

    async def rerank(self, query, documents):
        self.calls.append((query, list(documents)))
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._raises:
            raise self._raises
        n = len(documents)
        if self._order is None:      # identity
            return [(i, float(n - i)) for i in range(n)]
        return self._order


def _run(searcher, texts=DOCS, query="q"):
    return asyncio.run(searcher.rerank(query, texts))


# ═══════════════════════════════════════════════════════════
# 1. 正常路径
# ═══════════════════════════════════════════════════════════

def test_按分数重排并回报成功():
    eng = FakeEngine([(2, 0.9), (0, 0.5), (3, 0.2), (1, 0.1)])
    order, report = _run(RerankSearcher(eng))

    assert order == [2, 0, 3, 1]
    assert report == {"error": None, "candidates": 4, "applied": True}
    assert eng.calls[0] == ("q", DOCS), "送进引擎的查询是原词、文本原样"


def test_同分保留输入序():
    """稳定排序：分数一样时顺序不该被 dict / set 的迭代序左右。

    不稳定排序的表现是「同一份输入跑两次，第三条和第五条互换」—— 结果看起来
    完全正常，只有对比两次输出才发现。
    """
    eng = FakeEngine([(3, 0.5), (1, 0.5), (2, 0.5), (0, 0.5)])
    order, _ = _run(RerankSearcher(eng))
    assert order == [0, 1, 2, 3]


def test_缺失下标按原序追加到尾部():
    """引擎允许只返回前 k 条（`top_n` 语义）。漏掉的必须补回去，不能丢。"""
    eng = FakeEngine([(3, 0.9), (1, 0.8)])
    order, report = _run(RerankSearcher(eng))

    assert order == [3, 1, 0, 2], "未打分的按原下标升序垫在后面"
    assert report["applied"] is True


def test_非整数或越界的分数条目被丢弃():
    """引擎的返回是外部数据。`(0, "high")` / `(9, 1.0)` 不该让整次检索崩。"""
    eng = FakeEngine([(0, "high"), (9, 1.0), (2, 0.7)])
    order, _ = _run(RerankSearcher(eng))
    assert order == [2, 0, 1, 3]


# ═══════════════════════════════════════════════════════════
# 2. 短路径：不发起注定没意义的调用
# ═══════════════════════════════════════════════════════════

def test_候选不足两条时不调引擎():
    """一条候选重排前后必然是同一条 —— 那是一次纯计费、零收益的调用。"""
    eng = FakeEngine()
    for texts in ([], ["only"]):
        order, report = _run(RerankSearcher(eng), texts=texts)
        assert order == list(range(len(texts)))
        assert report["applied"] is False and report["error"] is None
    assert eng.calls == []


def test_precheck_有原因时不调引擎():
    """引擎在、但用不了（没配 Key / 缺模型文件）。原因要原样交给用户。"""
    eng = FakeEngine(precheck_reason="未配置 DashScope API Key")
    order, report = _run(RerankSearcher(eng))

    assert order == [0, 1, 2, 3]
    assert report["applied"] is False
    assert "DashScope API Key" in report["error"]
    assert eng.calls == []


def test_precheck_自己抛错也不炸():
    class Bad(FakeEngine):
        def precheck(self):
            raise OSError("模型文件损坏")

    order, report = _run(RerankSearcher(Bad()))
    assert order == [0, 1, 2, 3]
    assert "OSError" in report["error"]


def test_没有_precheck_钩子的引擎视为就绪():
    """云端引擎可选地不实现这三个钩子（都不进 Protocol）。"""

    class Bare:
        name = "bare"
        model_id = "bare"
        max_documents = 0

        async def rerank(self, query, documents):
            # 分数递增 ⇒ 排序结果必须是**倒序**，与 identity 分得开
            return [(i, float(i)) for i in range(len(documents))]

    order, report = _run(RerankSearcher(Bare()))
    assert order == [3, 2, 1, 0], "钩子缺失不等于不排 —— 当成 identity 就静默没重排"
    assert report["error"] is None and report["applied"] is True


# ═══════════════════════════════════════════════════════════
# 3. max_documents 截断
# ═══════════════════════════════════════════════════════════

def test_超过上限只送前N条_其余按原序垫在尾部():
    """服务端对超限返 400（实测 501 条）。截断是**本地**的责任。

    ⚠️ 被截掉的那批**不能丢**：它们本来就在候选池里（融合阶段就已经是搜到的东西），
    丢了等于「开了重排之后候选变少」—— 那条损失会混进实验的收益里。
    """
    eng = FakeEngine([(0, 0.9), (1, 0.1)], max_documents=2)
    texts = ["a", "b", "c", "d", "e"]
    order, report = _run(RerankSearcher(eng), texts=texts)

    assert eng.calls[0][1] == ["a", "b"], "只送前 max_documents 条"
    assert report["candidates"] == 2
    assert order == [0, 1, 2, 3, 4], "截掉的按原序接在后面"


def test_max_documents_为0表示不设上限():
    eng = FakeEngine(max_documents=0)
    texts = [f"d{i}" for i in range(80)]
    _, report = _run(RerankSearcher(eng), texts=texts)
    assert report["candidates"] == 80
    assert len(eng.calls[0][1]) == 80


# ═══════════════════════════════════════════════════════════
# 4. 失败一律退回原序，绝不向上抛
# ═══════════════════════════════════════════════════════════

def test_超时退回原序且不抛():
    eng = FakeEngine(delay=2.0)
    order, report = _run(RerankSearcher(eng, timeout=0.05))

    assert order == [0, 1, 2, 3]
    assert report["applied"] is False
    assert "超时" in report["error"]
    # 文案里要带秒数 —— 用户调 `RERANK_TIMEOUT` 时得知道现在是多少
    assert "0.05" in report["error"] or ">" in report["error"]


def test_引擎抛错退回原序且不抛():
    eng = FakeEngine(raises=RuntimeError("HTTP 401 InvalidApiKey"))
    order, report = _run(RerankSearcher(eng))

    assert order == [0, 1, 2, 3]
    assert report["applied"] is False
    assert "RuntimeError" in report["error"]


def test_载入超时退回原序():
    class SlowLoad(FakeEngine):
        async def warmup(self):
            await asyncio.sleep(2.0)

    eng = SlowLoad()
    order, report = _run(RerankSearcher(eng, load_timeout=0.05))

    assert order == [0, 1, 2, 3]
    assert "载入超时" in report["error"]
    assert eng.calls == [], "载入不成功就不该发重排请求"


def test_载入失败退回原序():
    eng = FakeEngine(warmup_raises=ImportError("缺少 onnxruntime"))
    order, report = _run(RerankSearcher(eng))

    assert order == [0, 1, 2, 3]
    assert "载入失败" in report["error"]
    assert eng.calls == []


def test_取消照常向上抛():
    """⚠️ **不是失败**：用户按了停止，不该被报成「重排不可用」。"""
    eng = FakeEngine(raises=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        _run(RerankSearcher(eng))


def test_构造时不传引擎直接报错():
    """这个类的全部职责就是编排一个引擎 —— 没有引擎的实例是无意义的。"""
    with pytest.raises(ValueError):
        RerankSearcher(None)


def test_超时常量是保守值():
    """SDK 默认 300 秒是批处理级；在线检索不能等 30 秒。

    这条不钉死具体数字（实验实测 p95 后可以校准），只钉住量级没被改成
    「跟 SDK 一样」—— 那会让一次网络抖动卡住整个对话。
    """
    assert 1.0 <= RERANK_TIMEOUT <= 15.0


# ═══════════════════════════════════════════════════════════
# 5. 排序算子唯一性
# ═══════════════════════════════════════════════════════════

def test_排序算子只住在这里():
    """`search_fuse.py` 只负责「哪些候选、怎么拼尾部」，不自己排。

    两处各排一遍的失效形态：某次只改了一处（比如加了个新权重），另一处照旧
    —— 两条路径的结果在同一个查询上给出不同的排列，而两边都报成功。
    """
    import ast
    from pathlib import Path

    import app.services.search_fuse as fuse_mod

    src = Path(fuse_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    hits = [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "sorted"]
    assert hits == [], f"search_fuse.py 第 {hits} 行出现了 sorted —— 排序该在 rerank_search"

    # 对照实验：同一段检测逻辑在含 sorted 的源码上必须报警
    probe = ast.parse("def f(x):\n    return sorted(x)\n")
    assert [n for n in ast.walk(probe)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "sorted"], "检测器本身没区分力"
