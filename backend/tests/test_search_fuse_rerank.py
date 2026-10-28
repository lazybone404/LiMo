"""融合编排里的重排层 —— 接线、候选池口径、以及「失败退回原序」。

三个**改回去也不会红**的点：

1. **两条分支都要重排**。`fused_search` 有「单查询」与「原词 + 改写」两条
   完全独立的路径；只在一条上接重排 = 开了开关却只在部分提问上生效，而两条
   都返回看起来正常的结果。
2. **候选池按 `reranker.candidates` 取，不按 `limit`**。取 `limit` 的话重排只
   能在 10 条里挑，那时它的实测收益里混着「候选变少了」的损失，`_experiments/
   07-rerank/` 会得出偏低的结论。
3. **失败时顺序逐字等于 RRF**。不是「差不多」，是**逐字** —— 重排是锦上添花，
   它挂掉之后用户该拿到的就是没开重排时的那份结果。

⚠️ 这里不 mock `fused_search`：那样测的只是桩。字面路走真 `QueryService` 的
替身，融合与重排走真实现。
"""
import asyncio
from types import SimpleNamespace

from app.services.search_fuse import fused_search


# ═══════════════════════════════════════════════════════════
# 测试替身
# ═══════════════════════════════════════════════════════════

class _Svc:
    """`QueryService` 的最小子集。`search_resources` 按 limit 截断。"""

    def __init__(self, n: int = 12, *, bodies: bool = True):
        self.n = n
        self.has_bodies = bodies
        self.calls: list[tuple[str, int]] = []
        if not bodies:
            # 让 `getattr(svc, "bodies", None)` 拿到 None —— 模拟老版本的 svc
            self.bodies = None

    def search_resources(self, q, *, limit=20, cursor=None):
        self.calls.append((q, limit))
        items = [{"id": f"doc{i}", "title": f"标题 {i}", "snippet": f"…{q}…"}
                 for i in range(min(self.n, limit))]
        return SimpleNamespace(items=items)

    def bodies(self, ids):
        return {i: f"{i} 的正文 " * 60 for i in ids}

    def get_resource(self, doc_id):
        return {"id": doc_id, "title": f"标题 {doc_id}", "rawText": f"{doc_id} 的原文 " * 60}


class _Searcher:
    """语义路替身。`fuse` 返回「字面 id 逆序 + 一条语义独有命中」。"""

    def __init__(self, semantic=None):
        self.calls: list[tuple[str, int, int]] = []
        self.semantic = semantic or {"hits": 3}

    async def fuse(self, term, lexical_ids, *, limit, depth):
        self.calls.append((term, limit, depth))
        ids = list(reversed(lexical_ids))[:limit]
        if ids:
            ids.append(f"vec_{term}")       # 语义独有命中（字面路没有）
            ids = ids[:limit]
        return ids, self.semantic


class _Reranker:
    """重排替身。默认 identity（不改变顺序），可切换成倒序 / 失败 / 候选数。"""

    def __init__(self, order=None, *, error=None, candidates=50):
        self.candidates = candidates
        self.calls: list[tuple[str, list[str]]] = []
        self._order = order
        self._error = error

    async def rerank(self, query, texts):
        self.calls.append((query, list(texts)))
        n = len(texts)
        if self._error:
            return list(range(n)), {"error": self._error, "candidates": n, "applied": False}
        if self._order is not None:
            order = self._order
        else:
            order = list(range(n))
        return order, {"error": None, "candidates": n, "applied": True}


def _run(svc, searcher, q="机器学习", *, limit=5, extras=(), reranker=None):
    return asyncio.run(fused_search(
        svc, searcher, q, limit=limit, extra_queries=extras, reranker=reranker))


def _ids(out):
    return [h["id"] for h in out["hits"]]


# ═══════════════════════════════════════════════════════════
# 1. reranker=None ⇒ 与改造前逐字节相同
# ═══════════════════════════════════════════════════════════

def test_不开重排时_rerank_为_None_且顺序与纯融合一致():
    svc, searcher = _Svc(), _Searcher()
    out = _run(svc, searcher)

    assert out["rerank"] is None
    # 语义路把字面结果倒序，并把语义独有命中补进列表
    assert _ids(out)[0] == "doc11"
    assert searcher.calls[0][1] == 5, "没开重排时只要 limit 条"


def test_不开重排时不多取候选():
    """`want = max(limit, candidates)` **只在开了重排时**成立。"""
    svc, searcher = _Svc(), _Searcher()
    _run(svc, searcher, limit=5, reranker=None)
    assert searcher.calls[0][1] == 5

    svc2, searcher2 = _Svc(), _Searcher()
    _run(svc2, searcher2, limit=5, reranker=_Reranker(candidates=50))
    assert searcher2.calls[0][1] == 50, "开了重排就要取到 candidates 条"


# ═══════════════════════════════════════════════════════════
# 2. 两条分支都重排
# ═══════════════════════════════════════════════════════════

def test_单查询分支会重排():
    rr = _Reranker()
    out = _run(_Svc(), _Searcher(), reranker=rr)

    assert len(rr.calls) == 1, "单查询分支没接重排"
    assert out["rerank"]["applied"] is True
    assert len(out["rerank"]) and out["rerank"]["candidates"] > 0


def test_改写分支也会重排():
    """⚠️ 两条路径是独立代码（一个走 `_one`、一个走 `rrf`），只在一条上接重排
    等于开关在部分提问上失效。"""
    rr = _Reranker()
    out = _run(_Svc(), _Searcher(), extras=["机器学习 入门"], reranker=rr)

    assert len(rr.calls) == 1, "改写分支没接重排"
    assert out["rerank"]["applied"] is True


def test_重排的查询是原词而不是改写词():
    """改写词的任务是「多召回几条」，重排要回答的是「用户实际问的那个问题」。"""
    rr = _Reranker()
    _run(_Svc(), _Searcher(), q="原问题", extras=["改写甲", "改写乙"], reranker=rr)

    assert rr.calls[0][0] == "原问题"


def test_候选文本按原词选窗():
    """换一个词选窗，重排的收益里就混进「窗口选得更好」那一份，实验不再是单变量。"""
    from app.core.search import rerank_window

    svc, rr = _Svc(), _Reranker()
    _run(svc, _Searcher(), q="原问题", extras=["完全不同的改写词"], reranker=rr)

    texts = rr.calls[0][1]
    # 每条候选的文本都该等于用**原词**从该篇正文里选出的窗口
    expected_first = rerank_window(svc.bodies(["doc11"])["doc11"], "原问题")
    assert texts[0] == expected_first


# ═══════════════════════════════════════════════════════════
# 2b. 字幕 JSON 必须先展开成自然语言
# ═══════════════════════════════════════════════════════════
#
# ⚠️ 这一组守的是一个**产品缺陷**（2026-10-29 由 07-rerank 实验查出）：
# 库里 93%（141/152）的文档 rawText 是视频字幕的 `{"segments": [...]}`。字面检索
# 不受影响（二字组照样命中），但 cross-encoder 判的是「这段文字是否回答了问题」。
# 把 JSON 语法碎片喂进去之后，实测正确文档从第 1 名掉到第 35 名 —— 而同一份候选、
# 同一个模型，区别只在窗口内容。测试若不钉住这一点，修好之后任何人优化选窗
# 都可能顺手把它改回去，且全部指标只会「莫名为难」地差，不会报错。

_SUB_JSON = (
    '{"segments": ['
    '{"start": 0.0, "end": 2.5, "text": "今天聊聊大家最关心的问题"},'
    '{"start": 2.5, "end": 6.0, "text": "学外语和学理科其实是同一套办法"},'
    '{"start": 6.0, "end": 9.0, "text": "都是把零散的事实压缩成可复用的规则"}'
    ']}'
)


class _SubSvc(_Svc):
    """`bodies` 返回字幕 JSON + 笔记 —— 与 `search_index_text` 的真实拼装同形。"""

    def bodies(self, ids):
        return {i: f'{_SUB_JSON}\n\n## 我的笔记\n\n抽象与规则化是共通的。' for i in ids}


def test_字幕_JSON_先展开成自然语言再选窗():
    svc, rr = _SubSvc(), _Reranker()
    _run(svc, _Searcher(), q="学外语和学理科", reranker=rr)

    text = rr.calls[0][1][0]
    assert '"start"' not in text and '"segments"' not in text, (
        f"送进重排的还是 JSON 碎片：{text[:80]!r}"
    )
    assert "同一套办法" in text, "展开后应能取到说这句话的那一段"


def test_展开后笔记仍接在后面():
    """`search_index_text` 拼的是 rawText + 笔记；展开字幕不能把笔记一起丢掉
    —— 用户自己记下的那个词是字面命中的主要来源。"""
    svc, rr = _SubSvc(), _Reranker()
    _run(svc, _Searcher(), q="抽象与规则化", reranker=rr)

    text = rr.calls[0][1][0]
    assert "抽象与规则化是共通的" in text, "笔记被丢掉了"


def test_非字幕文档逐字不变():
    """普通网页/PDF 的 rawText 不是这种结构 —— 展开必须是**无操作**。"""
    from app.services.search_fuse import _plain_body

    plain = "## 标题\n\n这是一篇普通的网页文章，讲的是注意力机制。" * 30
    assert _plain_body(plain) == plain


def test_展开与生成侧的时间戳格式同源():
    """`generation/prompt._format_segments` 与 `_plain_body` 认的是同一件事。

    停掉同源之后，一处改了「什么算字幕 JSON」而另一处没改，失效是静默的：
    生成侧拿到原文、或重排侧拿到 JSON，两边都不报错。
    """
    from app.core.text_utils import split_segments_json
    from app.generation.prompt import _format_segments
    from app.services.search_fuse import _plain_body

    parsed = split_segments_json(_SUB_JSON)
    assert parsed is not None and len(parsed[0]) == 3

    # 同一份输入：生成侧出时间戳、重排侧出纯文本，但「认不认这份 JSON」必须一致
    assert "00:02 - 学外语和学理科其实是同一套办法" in _format_segments(_SUB_JSON)
    assert "同一套办法" in _plain_body(_SUB_JSON)

    # 负向对照：不是字幕结构时两边都不认
    plain = "普通正文，没有 segments 字段"
    assert split_segments_json(plain) is None
    assert _format_segments(plain) == plain
    assert _plain_body(plain) == plain


# ═══════════════════════════════════════════════════════════
# 3. 候选池与结果截断
# ═══════════════════════════════════════════════════════════

def test_改写分支不把候选池压回_limit():
    """⚠️ `rrf(...)[:limit]` 是改造前的写法。开着重排却仍切 limit ⇒ 只在 5 条里挑。"""
    svc, searcher = _Svc(n=40), _Searcher()
    rr = _Reranker(candidates=30)
    _run(svc, searcher, limit=5, extras=["改写"], reranker=rr)

    assert len(rr.calls[0][1]) > 5, "候选池被 limit 截断了"
    assert len(rr.calls[0][1]) == 30, "合并后要切到 candidates 条再送重排"


def test_送进重排的候选数不少于_limit():
    rr = _Reranker(candidates=12)
    out = _run(_Svc(n=20), _Searcher(), limit=5, reranker=rr)
    assert len(rr.calls[0][1]) >= 5
    assert len(out["hits"]) == 5, "最终仍只返回 limit 条"


def test_重排后仍按_limit_截断():
    # 候选池 13 条（12 篇字面倒序 + 1 条语义独有），倒序重排后前 5 条是从没排过分的尾部开始
    rr = _Reranker(order=list(reversed(range(12))))
    out = _run(_Svc(n=12), _Searcher(), limit=5, reranker=rr)

    assert len(out["hits"]) == 5, "重排只改顺序，不改返回条数"
    baseline = _run(_Svc(n=12), _Searcher())["hits"]
    assert out["hits"][0]["id"] != baseline[0]["id"], "顺序没被重排改变 = 这条测试没有区分力"


# ═══════════════════════════════════════════════════════════
# 4. `rerank` 四态
# ═══════════════════════════════════════════════════════════

def test_第四态_失败时顺序逐字等于_RRF():
    """重排挂掉之后，用户该拿到的就是**没开重排时的那份结果**。

    「逐字」不是修辞：这里比对的是两个独立跑出来的 id 列表，任何一条位置不同
    都算回归 —— 因为失败路径的人为改动最容易变成「我顺手调整了一下兜底顺序」。
    """
    baseline = _ids(_run(_Svc(), _Searcher()))

    rr = _Reranker(error="重排超时（>5s），本次沿用融合顺序")
    out = _run(_Svc(), _Searcher(), reranker=rr)

    assert _ids(out) == baseline
    assert out["rerank"] == {
        "error": "重排超时（>5s），本次沿用融合顺序",
        "candidates": len(rr.calls[0][1]),
        "applied": False,
    }


def test_第三态_候选不足两条时_applied_为假且无错误():
    """一条候选重排前后必然相同 —— 那不是失败，只是没做事。

    ⚠️ 这条用**真的** `RerankSearcher`：`n < 2` 的短路住在编排层，替身里没有。
    用一个不短路的替身测这条，测的只是我自己的桩。
    """
    from app.services.rerank_search import RerankSearcher

    class _Eng:
        name = "e"
        model_id = "e"
        max_documents = 500

        def __init__(self):
            self.calls = []

        async def rerank(self, query, documents):
            self.calls.append((query, list(documents)))
            return [(0, 1.0)]

    class _Single:
        async def fuse(self, term, lexical_ids, *, limit, depth):
            return list(lexical_ids)[:1], {"hits": 1}

    eng = _Eng()
    out = _run(_Svc(n=1), _Single(), limit=5, reranker=RerankSearcher(eng))

    assert out["rerank"]["applied"] is False
    assert out["rerank"]["error"] is None
    assert eng.calls == [], "一条候选不该发起计费调用"


def test_第一态_没开时是_null_而不是空对象():
    """`null` 与 `{applied: false}` 的区别是「压根没开」与「开了没做事」。"""
    out = _run(_Svc(), _Searcher())
    assert out["rerank"] is None
    assert "rerank" in out, "键必须在场 —— 消费方靠它判断前端要不要显示这条自证"


# ═══════════════════════════════════════════════════════════
# 5. 候选文本的兜底链
# ═══════════════════════════════════════════════════════════

def test_没有正文时退回_snippet_再去标题():
    class _Flat:
        """字面顺序原样返回，不掺向量命中 —— 让下标与断言一一对应。"""

        async def fuse(self, term, lexical_ids, *, limit, depth):
            return list(lexical_ids)[:limit], {"hits": 0}

    svc = _Svc(n=3, bodies=False)
    svc.search_resources = lambda q, *, limit=20, cursor=None: SimpleNamespace(
        items=[{"id": "a", "title": "", "snippet": "<mark>命中</mark>的摘要"},
               {"id": "b", "title": "只有标题", "snippet": ""},
               {"id": "c", "title": "", "snippet": ""}])
    svc.get_resource = lambda doc_id: {"id": doc_id, "title": "", "rawText": ""}

    rr = _Reranker()
    _run(svc, _Flat(), reranker=rr)

    texts = rr.calls[0][1]
    assert texts[0] == "命中的摘要", "<mark> 必须剥掉 —— 它是展示层的高亮标记"
    assert texts[1] == "只有标题"
    assert texts[2] == "c", "全空时用 id 兜底，绝不送空串（服务端对空项行为未测）"


def test_送进引擎的列表里不存在空串():
    svc = _Svc(n=4, bodies=False)
    svc.get_resource = lambda doc_id: {"id": doc_id, "title": "", "rawText": ""}
    svc.search_resources = lambda q, *, limit=20, cursor=None: SimpleNamespace(
        items=[{"id": f"d{i}", "title": "", "snippet": ""} for i in range(4)])

    rr = _Reranker()
    _run(svc, _Searcher(), reranker=rr)
    assert all(t.strip() for t in rr.calls[0][1])


def test_bodies_抛错时改走读库兜底():
    """取候选正文失败只让兜底链降一档，不该毁掉整次搜索。"""
    svc = _Svc(n=3)
    svc.bodies = lambda ids: (_ for _ in ()).throw(RuntimeError("sqlite 锁住了"))

    rr = _Reranker()
    out = _run(svc, _Searcher(), reranker=rr)

    assert out["rerank"]["applied"] is True
    assert rr.calls[0][1], "读库兜底没兜住"
