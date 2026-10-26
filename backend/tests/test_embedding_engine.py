"""向量化引擎（`embedding` 类别）的契约测试。

运行：./.venv/Scripts/python.exe -m pytest tests/test_embedding_engine.py -q

**这一条测的不是「能不能调通 API」**（那需要真 Key、真网络、真计费），而是
`embedding_base` 模块文档写下的**三条契约**是否真的被实现：

  ① 返回顺序与入参一一对应（按 `index` 重排，不是按返回顺序 append）
  ② 向量已 L2 归一化（余弦可退化成点积）
  ③ 空输入返回空、且**不发起任何调用**

外加两条「照抄文档会出事」的硬约束（批次 ≤ 10、维度必须自洽），以及失败语义
（无 Key 直接抛、条数/维度不符直接抛，一律不降级）。

⚠️ 为什么这些值得单独写一轮：三条契约**全都有「单测全绿而线上错」的形态** ——
假数据天然有序（①恰好成立）、向量模长恰好接近 1（②看不出来）、空输入走了 client
也看不出来（只是多花一次请求）。真实数据的错位只有检索结果「莫名其妙」，不像错误。
"""

import asyncio

import pytest

# ⚠️ 不写 `sys.path.insert` —— `conftest.py` 负责那一行，新文件不要再各写一份。
from app.core.registry import EngineRegistry
from app.engines import init_engines
from app.engines.embedding_base import EmbeddingEngine, l2_normalize
from app.engines.embedding_dashscope import BATCH_SIZE, DashScopeEmbeddingEngine

DIM = DashScopeEmbeddingEngine.dimension


# ═══════════════════════════════════════════════════════════════
# 假上游（只模拟我们依赖的那一个方法：client.embeddings.create）
# ═══════════════════════════════════════════════════════════════

class _Item:
    def __init__(self, index: int, embedding: list[float]):
        self.index = index
        self.embedding = embedding


class _Resp:
    def __init__(self, data: list[_Item]):
        self.data = data


class _FakeEmbeddings:
    """`create(**kwargs)` 记录每次调用的入参，返回 builder 造出来的 data。"""

    def __init__(self, builder):
        self._builder = builder
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Resp(self._builder(kwargs["input"]))


class _FakeClient:
    def __init__(self, builder):
        self.embeddings = _FakeEmbeddings(builder)


def _vec(*head: float) -> list[float]:
    """造一个 DIM 维向量：前几个分量给定，其余补 0（省得手写 1024 个数字）。"""
    assert len(head) <= DIM
    return list(head) + [0.0] * (DIM - len(head))


def _engine_with(builder, api_key: str = "sk-fake") -> tuple[DashScopeEmbeddingEngine, _FakeClient]:
    """装上假 client 的引擎。`_client` 直接赋值 —— property 只在它为 None 时才构造。"""
    eng = DashScopeEmbeddingEngine(api_key=api_key)
    fake = _FakeClient(builder)
    eng._client = fake  # noqa: SLF001 —— 测试注入点，见文件头
    return eng, fake


def _ordered_builder(texts: list[str]) -> list[_Item]:
    """按**批内**下标造可区分向量（第 i 条第 i 维为 1）。只适用于单批。"""
    return [_Item(i, _vec(*([0.0] * i + [1.0]))) for i in range(len(texts))]


def _builder_by_text(texts: list[str]) -> list[_Item]:
    """按**文本里的编号**造向量（`t7` → 第 7 维为 1）—— 跨批也不会错位。

    ⚠️ 多批场景**不能用 `_ordered_builder`**：它拿到的是切片后的批，第 0 条永远是
    第 0 维，于是「跨批内容与向量对应」这件事在假数据上恒真，断言也就没有区分力。
    """
    return [_Item(i, _vec(*([0.0] * int(t[1:]) + [1.0]))) for i, t in enumerate(texts)]


# ═══════════════════════════════════════════════════════════════
# ① 协议形状与 L2 归一化工具
# ═══════════════════════════════════════════════════════════════

def test_协议形状():
    """`EmbeddingEngine` 是 Protocol —— 只要属性与方法对上就算满足（不要求继承）。"""
    eng = DashScopeEmbeddingEngine(api_key="sk-fake")
    assert isinstance(eng, EmbeddingEngine)
    assert eng.name == "dashscope-embedding"
    assert eng.model_id == "text-embedding-v4"
    assert eng.dimension == 1024
    assert "embedding" in eng.capabilities


def test_l2_normalize_归一化与零向量():
    """`[3, 4]` → `[0.6, 0.8]`；全零原样返回（返回 NaN 比返回零向量更难查）。"""
    assert l2_normalize([3.0, 4.0]) == pytest.approx([0.6, 0.8])
    assert l2_normalize([0.0, 0.0]) == [0.0, 0.0]
    assert sum(x * x for x in l2_normalize([1.0, 2.0, 3.0])) == pytest.approx(1.0)


# ═══════════════════════════════════════════════════════════════
# ② 无 Key / 空输入：都在客户端之外就结束
# ═══════════════════════════════════════════════════════════════

def test_无_key_直接抛且不构造客户端():
    """Key 缺失 → RuntimeError（文案要能直接指导用户去哪配），且不留下半成品 client。

    ⚠️ 抛点刻意在 `client` property 而不是 `__init__`：`init_engines` 无条件注册这个
    引擎，设置页要在「没配 Key」时也能列出它。若搬到 `__init__`，注册那一步就会
    被 `except Exception` 吞掉 → 引擎整个消失，用户看不到「有这个东西、只是没配」。
    """
    eng = DashScopeEmbeddingEngine(api_key="")
    with pytest.raises(RuntimeError, match="DashScope API Key"):
        asyncio.run(eng.embed(["你好"]))
    assert eng._client is None, "失败后不该留下半构造的 client"


def test_空输入返回空且不发起调用():
    """空列表 → `[]`，且**根本走不到 client**（没 Key 也不该抛）。"""
    eng = DashScopeEmbeddingEngine(api_key="")
    assert asyncio.run(eng.embed([])) == []
    assert eng._client is None, "空输入不该触发 client 构造——否则白白要求一份 Key"


# ══════════════════════════════════════════════════════════════
# ③ 三条契约 + 两条硬约束
# ═══════════════════════════════════════════════════════════════

def test_向量已归一化():
    """上游给 `[3, 4, 0, …]`，出口必须是模长 1（前两分量 0.6 / 0.8）。"""
    eng, _ = _engine_with(lambda texts: [_Item(i, _vec(3.0, 4.0)) for i in range(len(texts))])
    out = asyncio.run(eng.embed(["a", "b"]))
    assert len(out) == 2
    assert out[0][:2] == pytest.approx([0.6, 0.8])
    assert sum(x * x for x in out[0]) == pytest.approx(1.0)


def test_按_index_重排而不是按返回顺序():
    """上游乱序返回（data = [index 1, index 0]）时，出口仍需与入参对齐。

    ⚠️ 这是最容易「单测全绿而线上错位」的一条：假数据天然有序，而 OpenAI 兼容端点
    的 `data` **带 index 字段但不保证顺序**。错位的症状是检索结果莫名其妙，不像错误。
    """
    def reversed_builder(texts):
        # 倒序返回：最后一条先出来
        return [_Item(i, _vec(*([0.0] * i + [1.0]))) for i in reversed(range(len(texts)))]

    eng, _ = _engine_with(reversed_builder)
    out = asyncio.run(eng.embed(["第一条", "第二条", "第三条"]))
    for i, vec in enumerate(out):
        assert vec[i] == pytest.approx(1.0), f"第 {i} 条向量错位：{vec[:4]}"


def test_批处理每条请求不超十条():
    """11 条 → 2 次调用，切片为 10 + 1，且拼接顺序不乱。

    ⚠️ `BATCH_SIZE = 10` 是服务端硬上限（超了 HTTP 400），不是调优旋钮。
    """
    eng, fake = _engine_with(_builder_by_text)
    texts = [f"t{i}" for i in range(11)]
    out = asyncio.run(eng.embed(texts))

    assert len(out) == 11
    sizes = [len(c["input"]) for c in fake.embeddings.calls]
    assert sizes == [BATCH_SIZE, 1], f"分批不对：{sizes}"
    assert all(len(c["input"]) <= BATCH_SIZE for c in fake.embeddings.calls)
    for i, vec in enumerate(out):
        assert vec[i] == pytest.approx(1.0), f"第 {i} 条跨批错位"


def test_条数不符显式报错():
    """少回一条 → 直接抛。不补不猜：整体错位比报错难查得多。"""
    eng, _ = _engine_with(lambda texts: [_Item(i, _vec(1.0)) for i in range(len(texts) - 1)])
    with pytest.raises(RuntimeError, match="条数不符"):
        asyncio.run(eng.embed(["a", "b", "c"]))


def test_维度不符显式报错():
    """上游回了 512 维 → 当场抛。写进库之后余弦会在不同维度间静默算错。"""
    eng, _ = _engine_with(lambda texts: [_Item(i, [0.1] * 512) for i in range(len(texts))])
    with pytest.raises(RuntimeError, match="维度不符"):
        asyncio.run(eng.embed(["a"]))


def test_index_集合不符显式报错():
    """条数对得上、但 `index` 是 `[0, 0, 1]`（重复 + 缺号）→ 必须抛。

    ⚠️ **这是「条数校验挡不住」的那一格**：`sorted` 之后数组长度照样是 3，
    而「第三条向量对应哪段文本」已经不可知 —— 继续走下去就是本模块开头说的
    静默错位（检索结果莫名其妙、零报错、单测全绿）。
    对照实验：把 `indexes != list(range(...))` 那条判断删掉 → 本条红。
    """
    dup = [_Item(0, _vec(1.0)), _Item(0, _vec(2.0)), _Item(1, _vec(3.0))]
    eng, _ = _engine_with(lambda texts: dup)
    with pytest.raises(RuntimeError, match="index 不是"):
        asyncio.run(eng.embed(["a", "b", "c"]))


def test_index_越界显式报错():
    """条数对、`index` 是 `[1, 2, 3]`（整体偏移）→ 同样抛。

    ⚠️ 单测「重复」还不够：偏移形态在 `sorted` 之后**看起来完全有序**，
    是另一种更隐蔽的错位来源。
    """
    shifted = [_Item(i + 1, _vec(*([0.0] * i + [1.0]))) for i in range(3)]
    eng, _ = _engine_with(lambda texts: shifted)
    with pytest.raises(RuntimeError, match="index 不是"):
        asyncio.run(eng.embed(["a", "b", "c"]))


# ═══════════════════════════════════════════════════════════════
# ④ 注册可见（发现机制与其余引擎一视同仁）
# ═══════════════════════════════════════════════════════════════

def test_init_engines_后注册可见(monkeypatch):
    """`init_engines` 无条件注册它（与 OCR 同款）—— 没配 Key 也要列得出来。

    ⚠️ **`save_config` 必须被打桩**：`init_engines` 末尾会因「自动绑定 doc 引擎」判定
    有变更而调 `save_config(config)`，那条路径指着**用户真实的** `~/.limo/config.yaml`。
    隔离不能靠「我传了空 dict 所以它没事」这种直觉（那依赖 `save_config` 内部的防御性
    校验一直存在），要用断言回答「这个进程有没有写盘」：打桩时记一次数，末尾断言它
    确实被调用过 —— 既证明路径走到了，也证明**走的不是真函数**（模式 #58）。
    """
    import app.core.config_io as config_io

    write_attempts: list[dict] = []
    monkeypatch.setattr(config_io, "save_config", lambda cfg: write_attempts.append(cfg))

    init_engines({})

    names = EngineRegistry.list_category("embedding")
    assert "dashscope-embedding" in names, f"未注册，实际 {names}"
    eng = EngineRegistry.get("embedding", "dashscope-embedding")
    assert eng is not None
    assert eng.dimension == DIM
    assert write_attempts, "init_engines 没走到保存分支？那条路径是打桩自证的锚点"
