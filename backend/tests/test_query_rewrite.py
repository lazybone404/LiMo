"""对话侧查询改写 —— 形态是「原词 + 改写一起进 RRF」，不是「用改写替掉原词」。

数字来自 `_experiments/06-query-rewrite/`（44 题 · 融合路）：原词 H@1 52.3% / H@5 88.6%，
原+改 75.0% / 97.7%。H@5 最高的那一路**留着原词**的理由不止分数：改写失手时原词自动兜底。

本文件钉五件事，每件都对应一种「改回去也不会红」的静默失效：

1. **关思考是无条件的**：写成 `disable_thinking=llm.supports_reasoning` 会让 DeepSeek
   把 300 token 预算全烧在推理链上、`content` 空串，而现象看起来像「解析器坏了」。
2. **失败返回空列表而不是抛错**：改写是锦上添花的一步，它失败不该让「搜文档」失败。
3. **开关默认关**：打开它 = 每轮多一次计费调用，默认值必须等于改造前的行为。
4. **原词仍然检索**：只跑改写不跑原词，等于把兜底那一路删掉。
5. **prompt 与实验脚本同源**：改了 prompt 就必须重跑实验，否则那些数字描述的是
   另一套实现 —— 而两边都"能跑"。
"""
import ast
import asyncio
import inspect
from pathlib import Path

import pytest

from app.llm import CancelledError as LlmCancelledError
from app.routers.chat import _make_search_docs, _rewrite_enabled
from app.services import query_rewrite as qr
from app.services.query_rewrite import (
    MAX_QUERIES,
    MAX_QUERY_CHARS,
    REWRITE_PROMPT,
    rewrite_queries,
)
from app.services.search_fuse import fused_search

# ═══════════════════════════════════════════════════════════
# 测试替身
# ═══════════════════════════════════════════════════════════


class _LLM:
    """`LLMService` 的最小子集 —— 只记调用参数、返回预置文本。"""

    def __init__(self, text: str = "", *, error: Exception | None = None,
                 delay: float = 0.0):
        self.text = text
        self.error = error
        self.delay = delay
        self.calls: list[dict] = []

    async def call(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.text


class _Svc:
    """`QueryService` 的最小子集 —— 按 term 返回**不同**的文档，好看出合并效果。"""

    def __init__(self, mapping: dict[str, list[str]] | None = None):
        self.mapping = mapping or {}
        self.calls: list[tuple[str, int]] = []

    def search_resources(self, q, *, limit=20, cursor=None):
        self.calls.append((q, limit))
        ids = self.mapping.get(q, [f"{q}-0", f"{q}-1"])
        items = [{"id": i, "title": i, "snippet": f"正文含 {q}"} for i in ids]
        return type("Page", (), {"items": items[:limit]})()

    def get_resource(self, doc_id):
        return {"id": doc_id, "title": doc_id}


class _Searcher:
    """`VectorSearcher` 的最小子集 —— 只用来观察 `semantic` 取的是哪一次。"""

    def __init__(self):
        self.calls: list[str] = []

    async def fuse(self, query, lexical_ids, *, limit, depth):
        self.calls.append(query)
        hits = 1 if query == "原词" else 0
        return lexical_ids[:limit], {"error": None, "hits": hits}


def _call(llm, question, **kwargs):
    return asyncio.run(rewrite_queries(llm, question, **kwargs))


def _run_docs(fn, **kwargs):
    import json
    return json.loads(asyncio.run(fn(**kwargs)))


# ═══════════════════════════════════════════════════════════
# 1. 解析与清洗
# ═══════════════════════════════════════════════════════════


def test_正常解析出检索式():
    llm = _LLM('{"queries": ["投机解码", "草稿模型"], "keywords": "投机 解码"}')
    assert _call(llm, "那个一边猜一边验证的技术") == ["投机解码", "草稿模型"]


def test_容忍代码块围栏与前后说明():
    """模型常见多包一层 ```json —— `parse_json_object` 逐个 `{` 试 `raw_decode`。"""
    llm = _LLM('好的，结果如下：\n```json\n{"queries": ["投机解码"]}\n```\n以上。')
    assert _call(llm, "q") == ["投机解码"]


def test_keywords字段不被使用():
    """关键词臂被实验否决（H@1 68.2% vs 改写 77.3%，专有名词题更差）。

    它仍在 prompt 里是因为 prompt 是被实验测过的那个；实现只用 `queries`。
    """
    llm = _LLM('{"queries": ["甲"], "keywords": "乙 丙"}')
    assert _call(llm, "q") == ["甲"]


def test_最多取三条():
    llm = _LLM('{"queries": ["一", "二", "三", "四", "五"]}')
    assert len(_call(llm, "q")) == MAX_QUERIES


def test_与原词逐字相同的被丢弃():
    """原词本来就要检索一次，再搜一遍只是白花时间。"""
    llm = _LLM('{"queries": ["原问题", "别的说法"]}')
    assert _call(llm, "原问题") == ["别的说法"]


def test_重复的检索式只留一条():
    llm = _LLM('{"queries": ["甲", "甲", "乙"]}')
    assert _call(llm, "q") == ["甲", "乙"]


def test_超长截断而非丢弃():
    """截断后的前半段仍是一段可用的中文查询串；丢弃等于静默少一路召回。"""
    long = "检索" * 40
    llm = _LLM('{"queries": ["%s"]}' % long)
    out = _call(llm, "q")
    assert out == [long[:MAX_QUERY_CHARS]]


def test_去掉书名号引号():
    llm = _LLM('{"queries": ["「投机解码」", "\\"草稿模型\\""]}')
    assert _call(llm, "q") == ["投机解码", "草稿模型"]


@pytest.mark.parametrize("bad", ['{"queries": []}', '{"queries": "不是列表"}',
                                 "完全不是 JSON", '{"other": 1}'])
def test_抽不出检索式时返回空列表(bad):
    assert _call(_LLM(bad), "q") == []


# ═══════════════════════════════════════════════════════════
# 2. 调用的形状
# ═══════════════════════════════════════════════════════════


def test_必传关闭思考():
    """⚠️ 无条件关，**不是** `disable_thinking=llm.supports_reasoning`。

    那个能力位标的是「认不认 `reasoning_effort`」，而 DeepSeek 服务端思考默认开启、
    能力位又没标 ⇒ 关不掉 ⇒ 推理链烧光 300 token ⇒ `content` 空串。
    """
    llm = _LLM('{"queries": ["甲"]}')
    _call(llm, "q")
    assert llm.calls[0]["disable_thinking"] is True


def test_问题被嵌进_prompt():
    llm = _LLM('{"queries": ["甲"]}')
    _call(llm, "一边猜一边验证的技术")
    assert "一边猜一边验证的技术" in llm.calls[0]["prompt"]


@pytest.mark.parametrize("blank", ["", "   "])
def test_空问题不发起调用(blank):
    llm = _LLM('{"queries": ["甲"]}')
    assert _call(llm, blank) == []
    assert llm.calls == []


def test_已取消时不再发起调用():
    """用户按了停止之后还花钱发一次新调用，是最没意义的那种消费。"""
    ev = asyncio.Event()
    ev.set()
    llm = _LLM('{"queries": ["甲"]}')
    assert _call(llm, "q", cancel_event=ev) == []
    assert llm.calls == []


# ═══════════════════════════════════════════════════════════
# 3. 失败一律退化成原词检索
# ═══════════════════════════════════════════════════════════


def test_调用失败返回空列表():
    llm = _LLM(error=RuntimeError("服务端 500"))
    assert _call(llm, "q") == []


def test_超时返回空列表(monkeypatch):
    """超时独立一层：模型挂起时宁可退化，也不能让一次工具调用卡几分钟。"""
    monkeypatch.setattr(qr, "REWRITE_TIMEOUT", 0.02)
    llm = _LLM('{"queries": ["甲"]}', delay=0.2)
    assert _call(llm, "q") == []


def test_取消照常向上传播():
    """取消不是「改写失败」—— 把它吞成空列表会让整轮对话在用户已停止后继续跑。"""
    llm = _LLM(error=LlmCancelledError("用户取消"))
    with pytest.raises(LlmCancelledError):
        _call(llm, "q")


# ═══════════════════════════════════════════════════════════
# 4. fused_search 的 extra_queries
# ═══════════════════════════════════════════════════════════


def test_无改写时只检索一次():
    """零回归：`extra_queries` 为空 ⇒ 与改造前逐字相同的最后一步。"""
    svc = _Svc()
    out = asyncio.run(fused_search(svc, None, "q", limit=3))
    assert len(svc.calls) == 1
    assert out["semantic"] is None
    assert [h["id"] for h in out["hits"]] == ["q-0", "q-1"]


def test_每个改写各检索一次():
    svc = _Svc()
    asyncio.run(fused_search(svc, None, "q", limit=3, extra_queries=["a", "b"]))
    assert [c[0] for c in svc.calls] == ["q", "a", "b"]


def test_改写里的原词不重复检索():
    svc = _Svc()
    asyncio.run(fused_search(svc, None, "q", limit=3, extra_queries=["q", "a"]))
    assert [c[0] for c in svc.calls] == ["q", "a"]


def test_合并结果覆盖各路命中():
    svc = _Svc({"原词": ["A"], "改写一": ["B"], "改写二": ["C"]})
    out = asyncio.run(fused_search(svc, None, "原词", limit=5,
                                   extra_queries=["改写一", "改写二"]))
    assert {h["id"] for h in out["hits"]} == {"A", "B", "C"}


def test_RRF把两路都命中的排在前面():
    """`1/(k+rank)` 相加 —— 只在一条路里出现的文档永远排在两条路都有的之后。"""
    svc = _Svc({"原词": ["X", "共"], "改写": ["Y", "共"]})
    out = asyncio.run(fused_search(svc, None, "原词", limit=3, extra_queries=["改写"]))
    assert out["hits"][0]["id"] == "共"


def test_semantic取原词那一次():
    """口径不符 / 没建过 / 库不可用都是全局状态，改写那几次只会重复同一句话。"""
    svc, searcher = _Svc({"原词": ["A"], "改写": ["B"]}), _Searcher()
    out = asyncio.run(fused_search(svc, searcher, "原词", limit=3,
                                   extra_queries=["改写"]))
    assert out["semantic"]["hits"] == 1, "取的不是原词那次（替身对改写返回 hits=0）"
    assert searcher.calls == ["原词", "改写"]


def test_合并时每路都取到depth条():
    """⚠️ 每路只取 `limit` 条时，「原词第 3、改写第 7」这类组合永远进不了 H@5。"""
    svc = _Svc()
    asyncio.run(fused_search(svc, None, "q", limit=3, extra_queries=["a"]))
    assert all(c[1] >= 50 for c in svc.calls), f"取数深度不够：{svc.calls}"


# ═══════════════════════════════════════════════════════════
# 5. 工具层接线
# ═══════════════════════════════════════════════════════════


def test_开关关闭时一次改写都不发():
    """默认关 = 零回归：默认值必须等于改造前的行为（改造前没有这次调用）。"""
    llm, svc = _LLM('{"queries": ["甲"]}'), _Svc()
    _run_docs(_make_search_docs(type("S", (), {"list_transcripts": lambda *a: []})(),
                                svc, llm=llm, rewrite=False), query="q")
    assert llm.calls == []
    assert len(svc.calls) == 1


def test_开关打开时改写与原词都检索():
    llm, svc = _LLM('{"queries": ["别的说法"]}'), _Svc({"q": ["A"], "别的说法": ["B"]})
    out = _run_docs(_make_search_docs(type("S", (), {"list_transcripts": lambda *a: []})(),
                                      svc, llm=llm, rewrite=True), query="q")
    assert len(llm.calls) == 1
    assert [c[0] for c in svc.calls] == ["q", "别的说法"]
    assert {r["id"] for r in out["results"]} == {"A", "B"}


def test_没有_llm_时打开开关也不炸():
    svc = _Svc()
    _run_docs(_make_search_docs(type("S", (), {"list_transcripts": lambda *a: []})(),
                                svc, llm=None, rewrite=True), query="q")
    assert len(svc.calls) == 1


@pytest.mark.parametrize("cfg,expected", [
    ({}, False),
    ({"search": {}}, False),
    ({"search": {"rewrite_queries": True}}, True),
    ({"search": {"rewrite_queries": False}}, False),
    ({"search": None}, False),           # 脏配置不该炸
])
def test_开关默认关且容忍脏配置(cfg, expected):
    assert _rewrite_enabled(cfg) is expected


# ═══════════════════════════════════════════════════════════
# 6. prompt 与实验同源
# ═══════════════════════════════════════════════════════════


def test_prompt与实验脚本逐字一致():
    """⚠️ prompt 是被 44 题测过的那个。改它就等于让那些数字描述另一套实现。

    `_experiments/` 刻意不进仓库，故缺文件时跳过而不是失败 —— 但**本地跑得出来**
    的时候必须比对，否则这条守卫等于没有。
    """
    exp = (Path(__file__).resolve().parents[2]
           / "_experiments/06-query-rewrite/eval_rewrite.py")
    if not exp.exists():
        pytest.skip("实验脚本不在场（`_experiments/` 不进仓库）")
    src = exp.read_text(encoding="utf-8")
    assert REWRITE_PROMPT in src, (
        "prompt 与 `_experiments/06-query-rewrite/eval_rewrite.py` 不一致 —— "
        "改了 prompt 就必须重跑实验，否则 44 题的数字描述的是另一套实现")


def test_prompt把条数与字段写清楚了():
    """对照组：上面那条在缺文件时会跳过，这两条裸断言保证 prompt 本身没写歪。"""
    assert '"queries"' in REWRITE_PROMPT and '"keywords"' in REWRITE_PROMPT
    assert "2–3 条" in REWRITE_PROMPT


def test_不吞掉调用方的_llm():
    """连接池归调用方所有 —— 这里 `aclose()` 会让整轮对话的下一次调用失败。

    ⚠️ **走 AST 而不是 `"aclose" not in 源码`**：docstring 里就写着「绝不 `aclose()`」
    这句话，子串判会把它当成违规（第一版正是这么红的）。注释与文档在讲这件事，
    不代表代码在做这件事。
    """
    tree = ast.parse(inspect.getsource(rewrite_queries))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr != "aclose", "改写的 llm 归调用方所有，不能在这里关"
