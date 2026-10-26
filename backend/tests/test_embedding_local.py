"""本地向量引擎（`local-bge-small`）—— 协议契约 / 就绪自检 / 不阻塞事件循环。

运行：cd backend && pytest tests/test_embedding_local.py

⚠️ 本文件守的是四类**静默失效**：
  1. **顺序错位** —— 返回的向量与入参不是一一对应。检索照样返回结果，只是结果莫名其妙。
  2. **维度不符仍落库** —— 余弦在不同维度之间算出来的东西没有意义，而且不报错。
  3. **同步 ONNX 推理跑在事件循环里** —— 加载 3–5 秒期间后端没有任何响应（SSE 心跳、
     健康检查、前端请求一起停住），现象是「打开语义检索后应用卡死」，看起来像死锁。
  4. **构造有副作用** —— `init_engines` 无条件注册所有引擎（用户没装 onnxruntime 时
     设置页也要把它列出来并显示装什么），构造一旦 import 重依赖或加载模型就会
     让「打开设置页」变成「启动 3–5 秒」。
  5. **释放只置标志不丢句柄** —— `release()` 看起来成功了（返回 True、日志也打了），
     那 90MB 却一个字节都没还；反过来，门槛判反时越活跃的模型越先被踢掉，
     现象是「越用越慢」且零报错。

⚠️ **绝大多数用例用假 embedder**：真跑一次要 22.8MB 模型 + 3–5 秒加载，而这里要验的
是契约而不是 ONNX。唯一必须用真模型的只有「已 L2 归一化」—— 假 embedder 归不归一化
是测试自己决定的，验不到产品代码（该用例在缺依赖 / 缺模型时 skip，不伪装成通过）。
"""
import asyncio
import importlib.util
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import app.engines.embedding_local as embedding_local          # noqa: E402
from app.engines.embedding_local import (                     # noqa: E402
    DEFAULT_MODEL_DIR,
    DEFAULT_ONNX,
    LocalEmbeddingEngine,
)

#: 与产品侧 `precheck` **同一份判据**（`importlib.util.find_spec` 只查存在性、不加载）。
_HAS_DEPS = all(importlib.util.find_spec(m) is not None
                for m in embedding_local._REQUIRED_MODULES)


class _FakeEmbedder:
    """替身 ONNX 句柄。第 i 条输入 → 第 `i % dim` 位为 1 的单位向量。

    位置相关是**刻意的**：这样「顺序一一对应」可以被断言（否则所有向量长得一样，
    错位根本看不出来）。
    """

    def __init__(self, dim: int = 512, *, extra_rows: int = 0, delay: float = 0.0):
        self.dim = dim
        self._extra = extra_rows
        self._delay = delay

    def __call__(self, texts, bs: int = 32):
        import numpy as np

        if self._delay:
            time.sleep(self._delay)
        rows = []
        for i in range(len(texts)):
            v = np.zeros(self.dim, dtype=np.float32)
            v[i % self.dim] = 1.0
            rows.append(v)
        rows += [np.zeros(self.dim, dtype=np.float32) for _ in range(self._extra)]
        return np.vstack(rows)


def _engine(**kw) -> LocalEmbeddingEngine:
    return LocalEmbeddingEngine(embedder_factory=lambda *a: _FakeEmbedder(**kw))


# ── 协议契约 ──

def test_空输入返回空且不加载模型():
    """契约 3：空输入返回空、**且不发起任何调用**。

    ⚠️ 「不加载模型」这一半比「返回空列表」重要得多：查一个空批不该把 22.8MB 权重
    拖进内存，而两者都能让「返回空」这个断言通过。
    """
    built: list[int] = []

    def factory(*_a):
        built.append(1)
        return _FakeEmbedder()

    eng = LocalEmbeddingEngine(embedder_factory=factory)
    assert asyncio.run(eng.embed([])) == []
    assert built == [], "空输入不该构造 embedder（连模型加载都不该发生）"


def test_顺序一一对应():
    """契约 1：返回顺序与入参一一对应。

    错位的失效形态是静默的 —— 检索照样返回结果，只是「文本 A 的向量」挂在 B 上。
    """
    eng = _engine()
    vecs = asyncio.run(eng.embed(["a", "b", "c"]))
    assert [v.index(1.0) for v in vecs] == [0, 1, 2]


def test_条数不符显式报错():
    """引擎违约（多返回一条）必须抛 —— 补一条 / 猜一下都会让整批错位。"""
    eng = _engine(extra_rows=1)
    with pytest.raises(RuntimeError, match="条数不符"):
        asyncio.run(eng.embed(["a", "b"]))


def test_维度不符显式报错():
    """维度不符必须当场报：写进库之后，余弦会在不同维度之间静默算错。"""
    eng = _engine(dim=3)
    with pytest.raises(RuntimeError, match="维度不符"):
        asyncio.run(eng.embed(["a"]))


def test_embed_不阻塞事件循环():
    """⚠️ 本轮最关键的一条：ONNX 推理是**同步 CPU 计算**。

    直接在事件循环里跑，加载 / 推理期间后端没有任何响应（SSE 心跳、健康检查、
    前端请求一起停住），而现象是「打开语义检索后应用卡死」—— 一个看起来像死锁、
    实际是阻塞的失败形态。
    """
    eng = _engine(delay=0.3)
    ticks: list[int] = []

    async def _spinner():
        for _ in range(10):
            ticks.append(1)
            await asyncio.sleep(0.02)

    async def _go():
        await asyncio.gather(eng.embed(["x"]), _spinner())

    asyncio.run(_go())
    assert len(ticks) == 10, "spinner 被 embed 卡住了 —— 没走 asyncio.to_thread"


def test_并发预热只构造一次():
    """8 个并发 `warmup()` → 只构造一个 InferenceSession。

    没有那把锁会并行构造多个（内存翻倍），且后一个把前一个的句柄丢掉 ⇒
    每个仍持有它的一方都以为自己用的是唯一的那个。
    """
    built: list[int] = []

    def factory(*_a):
        time.sleep(0.05)          # 拉长构造窗口，让「不加锁」真的会撞
        built.append(1)
        return _FakeEmbedder()

    eng = LocalEmbeddingEngine(embedder_factory=factory)

    async def _go():
        await asyncio.gather(*[eng.warmup() for _ in range(8)])

    asyncio.run(_go())
    assert len(built) == 1


# ── 内存释放（`vector_worker.maybe_release` 用 getattr 消费的可选钩子） ──

def test_释放后重新加载():
    """`release()` 之后下一次 `embed` 必须**重新构造**句柄。

    ⚠️ 这条同时钉住「释放不是标记位」：只置一个 `_released` 标志而不丢 `_inst` 的话，
    内存一个字节都不会还，而返回形状 / 维度这些断言照样全过。
    """
    built: list[int] = []

    def factory(*_a):
        built.append(1)
        return _FakeEmbedder()

    eng = LocalEmbeddingEngine(embedder_factory=factory)
    asyncio.run(eng.embed(["a"]))
    assert len(built) == 1

    assert eng.release(after_idle=0) is True, "after_idle=0 = 立刻释放（immediate 档）"

    vecs = asyncio.run(eng.embed(["b"]))
    assert len(built) == 2, "释放后必须重新构造，不能复用已丢掉的句柄"
    assert len(vecs[0]) == 512


def test_空闲不足时不释放():
    """`idle` 档：刚用过就调 `release()` → 什么都不做，且**不丢句柄**。

    反向失效形态是静默的：门槛判反（把「不够久」写成「够久了」）时，越活跃的模型
    越先被踢掉 —— 现象是「越用越慢」，而没有任何报错。
    """
    built: list[int] = []

    def factory(*_a):
        built.append(1)
        return _FakeEmbedder()

    eng = LocalEmbeddingEngine(embedder_factory=factory)
    asyncio.run(eng.warmup())
    assert eng.release() is False             # 默认门槛 600s，刚预热过
    asyncio.run(eng.embed(["a"]))
    assert len(built) == 1, "没释放就不该重新构造"


def test_未加载时释放返回_False():
    """没加载过就没有可释放的 —— 返回 False 且**不构造**。

    否则「释放」会先花 3–5 秒把模型加载一遍再放掉，而调用方（每 60 秒一轮的空转
    worker）会以为一切正常。
    """
    built: list[int] = []

    def factory(*_a):
        built.append(1)
        return _FakeEmbedder()

    eng = LocalEmbeddingEngine(embedder_factory=factory)
    assert eng.release(after_idle=0) is False
    assert built == []


def test_warmup_已加载时是纯内存快路():
    """已加载时 `warmup()` 不重复构造 —— 它**每次查询**都会被调
    （`VectorSearcher._ensure_loaded`），没有快路的话每次搜索都白占一次线程池调度。"""
    built: list[int] = []

    def factory(*_a):
        built.append(1)
        return _FakeEmbedder()

    eng = LocalEmbeddingEngine(embedder_factory=factory)

    async def _go():
        await eng.warmup()
        await eng.warmup()

    asyncio.run(_go())
    assert len(built) == 1


# ── 就绪自检（`vector_runtime` 用 getattr 消费的可选钩子） ──

def test_precheck_缺依赖给安装命令(monkeypatch):
    """依赖没装时给出**可直接粘贴的命令**，而不是一句「不可用」。"""
    real = embedding_local.importlib.util.find_spec
    monkeypatch.setattr(
        embedding_local.importlib.util, "find_spec",
        lambda name, *a, **k: (None if name in embedding_local._REQUIRED_MODULES
                               else real(name, *a, **k)))

    reason = LocalEmbeddingEngine().precheck()
    assert reason, "缺依赖必须返回原因"
    assert "pip install onnxruntime tokenizers" in reason
    assert "pypi.tuna.tsinghua.edu.cn" in reason, "国内镜像（全局约定）"
    assert "不随应用分发" in reason, "使用条件必须说出来，不能只报缺哪个包"


def test_precheck_打包版不给跑不了的命令(monkeypatch):
    """冻结环境（`LiMoBackend.exe`）**没有 pip** —— 那一档必须直说做不到。

    ⚠️ 这条守的不是文案好看，而是**不给用户一条走不通的路**：安装版用户看到
    `pip install ...` 会反复试，以为自己操作错了。反向断言 `"pip install" not in reason`
    就是全部意义（改回「一律给命令」→ 立刻红）。
    """
    monkeypatch.setattr(embedding_local.sys, "frozen", True, raising=False)
    real = embedding_local.importlib.util.find_spec
    monkeypatch.setattr(
        embedding_local.importlib.util, "find_spec",
        lambda name, *a, **k: (None if name in embedding_local._REQUIRED_MODULES
                               else real(name, *a, **k)))

    reason = LocalEmbeddingEngine().precheck()
    assert reason
    assert "pip install" not in reason, "冻结环境没有 pip，这条命令跑不了"
    assert "无法在应用内补齐" in reason
    assert "云端" in reason, "说做不到的同时要给出这一档的出路"


@pytest.mark.skipif(not _HAS_DEPS, reason="本地向量依赖未安装")
def test_precheck_缺模型文件(tmp_path):
    """依赖齐了但模型文件不在 → 原因里要有**具体路径**（用户才知道去下什么）。"""
    reason = LocalEmbeddingEngine(model_dir=str(tmp_path)).precheck()
    assert reason and str(tmp_path) in reason
    assert "tokenizer.json" in reason


@pytest.mark.skipif(not _HAS_DEPS, reason="本地向量依赖未安装")
def test_precheck_齐备时返回_None(tmp_path):
    """三个文件齐了 → `None`（可以跑）。⚠️ 反向断言：只有真齐备才放行。"""
    (tmp_path / "tokenizer.json").write_text("{}", encoding="utf-8")
    (tmp_path / DEFAULT_ONNX).write_bytes(b"\x00")
    assert LocalEmbeddingEngine(model_dir=str(tmp_path)).precheck() is None


def test_构造零副作用(tmp_path):
    """模型目录根本不存在也**不抛** —— 抛点必须推迟到 precheck / warmup / embed。

    `init_engines` 无条件注册所有引擎，构造一抛就意味着「设置页里那一项消失了」，
    用户看不到它、也就不知道要装什么。
    """
    eng = LocalEmbeddingEngine(model_dir=str(tmp_path / "nope"))
    assert eng.precheck()          # 有原因，但构造本身没抛
    assert eng.name == "local-bge-small"


# ── 真模型：唯一必须真跑的一条 ──

def test_真模型_已归一化且维度正确():
    """契约 2：已 L2 归一化。**只能**用真 embedder 验（见模块文档）。

    ⚠️ 这条不是形式主义：`vector_index` 的余弦算的是**点积**（归一化后两者等价）。
    有人改 pooling / 换模型时忘了归一化，余弦会变成点积 —— 排序全乱、零报错。
    """
    if not _HAS_DEPS:
        pytest.skip("本地向量依赖未安装")
    d = Path(DEFAULT_MODEL_DIR).expanduser()
    if not (d / "tokenizer.json").exists() or not (d / DEFAULT_ONNX).exists():
        pytest.skip(f"本地模型不在场：{d}")

    eng = LocalEmbeddingEngine()
    vecs = asyncio.run(eng.embed(["第一段文本", "完全不同的第二段文本"]))

    assert len(vecs) == 2
    for v in vecs:
        assert len(v) == eng.dimension == 512
        norm = sum(x * x for x in v) ** 0.5
        assert abs(norm - 1.0) < 1e-4, f"未归一化：‖v‖={norm}"
    # 防「所有输出都一样」的退化（CLS pooling 取错位置时会出现）
    assert vecs[0] != vecs[1]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
