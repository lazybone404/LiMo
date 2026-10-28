"""DashScope 重排引擎 —— 响应形状、失败语义、以及**先判 status_code** 这条。

这个文件的重点是三条**不遵守也不报错、只是结果悄悄不对**的契约
（`rerank_base.RerankEngine` 的 1 / 2 条 + 失败分支）：

1. **非 200 时 `resp.output` 是 `None`**（不是缺这个属性 —— 实测 `hasattr` 为
   True）。直接 `.output.results` 会得到 `'NoneType' object has no attribute
   'results'`，用户看不到「Key 过期 / 配额不足 / 参数越界」中的哪一个。那条
   测试断言的是**异常类型与文案**，不是「抛了就行」。
2. **空 `documents` 不发起调用**。服务端会返 400（错误串里写着 "should not be
   empty"），而调用方在「候选不足」时正会传空列表。
3. **下标越界 / 重复直接抛**。不抛的话，别人的分数会贴到这篇头上，而结果列表
   看起来完全正常。

⚠️ **全程不联网**：`AioTextReRank.call` 被替换成替身。测试里出现的 `api_key`
是字面量 `"test-key"`，任何断言都不该让它在报错文案里出现（那条 Key 卫生的
断言在 `test_无_key_时报错不含任何片段` 里）。
"""
import asyncio

import pytest

from app.engines.rerank_base import RerankEngine
from app.engines.rerank_dashscope import MAX_DOCUMENTS, DashScopeRerankEngine


class _Item:
    def __init__(self, index, score):
        self.index = index
        self.relevance_score = score
        self.document = None


class _Output:
    def __init__(self, items):
        self.results = items


class _Resp:
    def __init__(self, items=None, *, status_code=200, code="", message="", tokens=0):
        self.status_code = status_code
        self.code = code
        self.message = message
        # ⚠️ 非 200 时是 **None 而不是不赋值**（实测 DictMixin 的行为）——
        # 正是这一点让「先判 status_code」成为必需而不是风格偏好。
        self.output = _Output(items) if status_code == 200 else None
        self.usage = type("U", (), {"total_tokens": tokens})()


def _patch_call(monkeypatch, resp_or_fn):
    calls: list[dict] = []

    async def _fake(**kwargs):
        calls.append(kwargs)
        return resp_or_fn(**kwargs) if callable(resp_or_fn) else resp_or_fn

    monkeypatch.setattr("app.engines.rerank_dashscope.AioTextReRank.call", _fake)
    return calls


def _engine(key="test-key") -> DashScopeRerankEngine:
    return DashScopeRerankEngine(api_key=key)


# ═══════════════════════════════════════════════════════════
# 1. 协议 & 元数据
# ═══════════════════════════════════════════════════════════

def test_满足重排引擎协议():
    assert isinstance(_engine(), RerankEngine)


def test_max_documents_是实测的服务端上限():
    """500 不是保守值 —— 探测脚本发 500 条返 200、501 与 800 条返 400。

    调大不会有收益，只会拿到 400；调小则是白丢候选（重排只能在召回内挑）。
    """
    assert _engine().max_documents == MAX_DOCUMENTS == 500


def test_展示名说得清算不算钱():
    """设置页下拉读它。缺了会显示裸的 `dashscope-rerank`，用户看不出成本。"""
    e = _engine()
    assert e.is_local is False
    assert "计费" in e.display_name


# ═══════════════════════════════════════════════════════════
# 2. 正常路径
# ═══════════════════════════════════════════════════════════

def test_返回原始下标与分数(monkeypatch):
    _patch_call(monkeypatch, _Resp([_Item(2, 0.9), _Item(0, 0.4), _Item(1, 0.1)]))

    out = asyncio.run(_engine().rerank("q", ["a", "b", "c"]))

    # ⚠️ 下标指向**入参位置**，不是结果里的新位置 —— 这条搞反 = 结果整体错位
    assert out == [(2, 0.9), (0, 0.4), (1, 0.1)]


def test_不传未测过的服务端超参(monkeypatch):
    """`instruct` / `top_n` 会改变排序口径，而实验基线不认识它们。"""
    calls = _patch_call(monkeypatch, _Resp([_Item(0, 0.5)]))

    asyncio.run(_engine().rerank("q", ["a"]))

    assert set(calls[0]) == {"model", "query", "documents", "api_key"}
    assert calls[0]["model"] == "gte-rerank-v2"
    assert calls[0]["documents"] == ["a"]


def test_空入参不发请求(monkeypatch):
    """契约 2。服务端对空 documents 返 400，而调用方在候选不足时正会传空。"""
    calls = _patch_call(monkeypatch, _Resp([]))

    assert asyncio.run(_engine().rerank("q", [])) == []
    assert calls == [], "空输入一个请求都不该发"


# ═══════════════════════════════════════════════════════════
# 3. 失败语义
# ═══════════════════════════════════════════════════════════

def test_无_key_时报错不含任何片段():
    """Key 的抛点在 `api_key` property，不在 `__init__`（无条件注册）。"""
    e = DashScopeRerankEngine(api_key="")
    with pytest.raises(RuntimeError) as ei:
        _ = e.api_key
    assert "DashScope API Key" in str(ei.value)
    assert "设置" in str(ei.value), "要说清去哪儿配"


def test_非200时先判状态码_而不是炸在_output上(monkeypatch):
    """⚠️ 这条钉的是**异常类型**，不是「抛了就行」。

    实测非 200 时 `resp.output` 是 `None`（`hasattr` 为 True），所以
    `resp.output.results` 抛的是 `AttributeError: 'NoneType' object has no
    attribute 'results'` —— 一句对用户毫无信息量的话。断言它**不是**
    AttributeError、且文案含服务端 message，才证明判断顺序是对的。
    """
    _patch_call(monkeypatch, _Resp(status_code=401, code="InvalidApiKey",
                                   message="Invalid API-key provided."))

    with pytest.raises(RuntimeError) as ei:
        asyncio.run(_engine().rerank("q", ["a"]))

    msg = str(ei.value)
    assert not isinstance(ei.value, AttributeError)
    assert "401" in msg and "Invalid API-key" in msg
    assert "NoneType" not in msg


def test_下标越界直接抛(monkeypatch):
    """不抛 = 别人的分数贴到这篇头上，而结果列表看起来完全正常。"""
    _patch_call(monkeypatch, _Resp([_Item(5, 0.9)]))

    with pytest.raises(RuntimeError, match="越界"):
        asyncio.run(_engine().rerank("q", ["a", "b"]))


def test_下标重复直接抛(monkeypatch):
    """同一段候选出现两次没有语义；两个分里哪个算数无从判断。"""
    _patch_call(monkeypatch, _Resp([_Item(0, 0.9), _Item(0, 0.1)]))

    with pytest.raises(RuntimeError, match="重复"):
        asyncio.run(_engine().rerank("q", ["a"]))


def test_空结果列表是合法响应(monkeypatch):
    """服务端可能对某些请求返回空 results —— 那不是错误，只是「一条都没排」。

    调用方（`RerankSearcher._apply_scores`）会把它们按原序追加回尾部。
    """
    _patch_call(monkeypatch, _Resp([]))
    assert asyncio.run(_engine().rerank("q", ["a", "b"])) == []
