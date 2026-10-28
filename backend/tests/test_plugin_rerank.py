"""重排插件（`RerankPlugin`）— 契约 / 常驻子进程 IPC / 加载分派。

判据落在**真子进程**上（`sys.executable` 跑一个假 server），不走 mock —— 理由同
`test_plugin_embedding.py`：这一层的失败形态几乎都是「协议对了但进程状态错了」
（管道缓冲、响应错位、进程已死），mock 掉 IO 就什么都测不到。

⚠️ 本文件独有的两条判据，是重排与向量化**形状上的差别**带来的：

- **`results` 的第一个数是「入参下标」**。搞反的结果是整张排名表错位，而每一行
  看起来都合法（分数是真的、下标是合法整数）⇒ 越界与重复必须**显式抛**。
- **允许只返回前 k 条**（`top_n` 语义），尾部由 `RerankSearcher` 按原序补齐 ⇒
  条数少于入参是**合法**的，不能当协议错误。

假 server 的行为由环境变量 `FAKE_MODE` 控制（adapter 起子进程时用
`os.environ.copy()`，测试进程里设的变量天然传得下去）。
"""
import ast
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from pathlib import Path

import pytest

from app.core.registry import EngineRegistry
from app.engines import plugin_loader as pl
from app.engines.plugin_loader import (
    _peek_plugin_meta,
    _PluginRerankAdapter,
    PLUGIN_REGISTRY,
    RerankPlugin,
    init_plugins,
)

EXAMPLES = Path(__file__).resolve().parent.parent / 'plugins' / 'examples'

# ── 假 server ──
# FAKE_MODE：ok / wrong_id / error_once / bad_line / missing_results / bad_item /
#            out_of_range / dup_index / partial / slow
# 每条都对应适配器里一条**必须显式失败**（或必须放行）的路径。
FAKE_SERVER = r'''
import json, os, sys, time
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

MODE = os.environ.get("FAKE_MODE", "ok")

def emit(o):
    sys.stdout.write(json.dumps(o) + "\n")
    sys.stdout.flush()

emit({"ready": True, "model_id": os.environ.get("FAKE_MODEL_ID", "fake-rerank-model")})

served = 0
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    rid = req.get("id")
    docs = req.get("documents") or []
    # 每次请求留一个痕迹（「空输入不发起调用」的判据载体）
    if os.environ.get("FAKE_CALL_LOG"):
        with open(os.environ["FAKE_CALL_LOG"], "a", encoding="utf-8") as f:
            f.write(str(len(docs)) + "\n")
    if MODE == "wrong_id":
        emit({"id": (rid or 0) + 100, "results": []})
        continue
    if MODE == "error_once" and served == 0:
        served += 1
        emit({"id": rid, "error": "boom"})
        continue
    served += 1
    if MODE == "bad_line":
        sys.stdout.write("not json\n")
        sys.stdout.flush()
        continue
    if MODE == "missing_results":
        emit({"id": rid})
        continue
    if MODE == "bad_item":
        emit({"id": rid, "results": [[0]]})      # 缺分数
        continue
    if MODE == "out_of_range":
        emit({"id": rid, "results": [[99, 1.0]]})
        continue
    if MODE == "dup_index":
        emit({"id": rid, "results": [[0, 1.0], [0, 0.5]]})
        continue
    if MODE == "slow":
        time.sleep(3)
    if MODE == "partial":
        emit({"id": rid, "results": [[0, 1.0]]})
        continue
    # 分数 = 文本长度（用来验「下标→分数」没有错配）
    emit({"id": rid, "results": [[i, float(len(d))] for i, d in enumerate(docs)]})
'''


class _RRPlugin(RerankPlugin):
    name = "test-rr"
    command = []                     # 每个用例改到实例上
    model_id = "test-rr-model"
    max_documents = 7
    display_name = "测试重排插件"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """每个用例从干净的环境变量开始（FAKE_* 会漏进子进程）。"""
    for k in ("FAKE_MODE", "FAKE_MODEL_ID", "FAKE_CALL_LOG"):
        monkeypatch.delenv(k, raising=False)
    yield


@pytest.fixture
def make_adapter(tmp_path):
    """造一个 adapter，用例结束后**确保杀掉子进程**（残留会拖慢整个测试会话）。"""
    script = tmp_path / "fake_server.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    made = []

    def _make(**attrs):
        plugin = _RRPlugin()
        plugin.command = [sys.executable, "-u", str(script)]
        for k, v in attrs.items():
            setattr(plugin, k, v)
        adapter = _PluginRerankAdapter(plugin, tmp_path, script)
        made.append(adapter)
        return adapter

    yield _make
    for a in made:
        a.release(after_idle=0) if hasattr(a, "release") else None


def _run(coro):
    return asyncio.run(coro)


# ── 契约 ──

def test_rerank_passes_through_index_and_score(make_adapter):
    """下标与分数如实转出 —— 中途不做重排、不做过滤。"""
    a = make_adapter()
    docs = ["a", "bbb", "cc"]
    out = _run(a.rerank("查询", docs))
    assert out == [(0, 1.0), (1, 3.0), (2, 2.0)], out


def test_rerank_empty_returns_empty_without_calling(make_adapter, tmp_path, monkeypatch):
    """契约 2：空输入返回 `[]`，且**连子进程都不拉起**。

    省的不是钱，是「一条必失败的请求」与「为它白加载一次 544MB 模型」。
    """
    log = tmp_path / "calls.log"
    monkeypatch.setenv("FAKE_CALL_LOG", str(log))
    a = make_adapter()
    assert _run(a.rerank("q", [])) == []
    assert a._proc is None, "空输入不该起子进程"
    assert not log.exists(), "空输入不该发起任何调用"


def test_rerank_allows_subset(make_adapter, monkeypatch):
    """契约 3：只返回前 k 条是**合法**的（`top_n` 语义），尾部由调用方补齐。"""
    monkeypatch.setenv("FAKE_MODE", "partial")
    a = make_adapter()
    assert _run(a.rerank("q", ["a", "b", "c"])) == [(0, 1.0)]


def test_rerank_out_of_range_index_raises(make_adapter, monkeypatch):
    """越界必须抛：下标算错时**别人的分数会贴到这篇头上**，而结果看起来完全正常。"""
    monkeypatch.setenv("FAKE_MODE", "out_of_range")
    a = make_adapter()
    with pytest.raises(Exception, match="越界"):
        _run(a.rerank("q", ["a", "b", "c"]))


def test_rerank_duplicate_index_raises(make_adapter, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "dup_index")
    a = make_adapter()
    with pytest.raises(Exception, match="重复"):
        _run(a.rerank("q", ["a", "b", "c"]))


def test_rerank_bad_item_shape_raises(make_adapter, monkeypatch):
    """`[[0]]`（缺分数）要报「不是 [下标, 分数]」，而不是裸 IndexError。"""
    monkeypatch.setenv("FAKE_MODE", "bad_item")
    a = make_adapter()
    with pytest.raises(Exception, match="下标"):
        _run(a.rerank("q", ["a", "b"]))


def test_rerank_missing_results_kills_proc(make_adapter, monkeypatch):
    """响应缺 `results` = 协议破了 ⇒ 丢进程（留着它下一轮还会这么回）。"""
    monkeypatch.setenv("FAKE_MODE", "missing_results")
    a = make_adapter()
    with pytest.raises(Exception, match="results"):
        _run(a.rerank("q", ["a"]))
    assert a._proc is None, "缺 results 的进程必须丢弃"


# ── 往返协议的四条（与向量化同源，这里只确认重排这一族也接上了）──

def test_wrong_id_kills_proc(make_adapter, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "wrong_id")
    a = make_adapter()
    with pytest.raises(Exception, match="错位"):
        _run(a.rerank("q", ["a"]))
    assert a._proc is None


def test_non_json_line_kills_proc(make_adapter, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "bad_line")
    a = make_adapter()
    with pytest.raises(Exception, match="非 JSON"):
        _run(a.rerank("q", ["a"]))
    assert a._proc is None


def test_error_response_keeps_proc(make_adapter, monkeypatch):
    """单次失败 ≠ 协议坏了：进程留着，下一次照常服务。"""
    monkeypatch.setenv("FAKE_MODE", "error_once")
    a = make_adapter()
    with pytest.raises(Exception, match="boom"):
        _run(a.rerank("q", ["a"]))
    assert a._proc is not None, "单次失败不该丢进程"
    assert _run(a.rerank("q", ["a"])) == [(0, 1.0)]


def test_request_timeout_kills_proc(make_adapter, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "slow")
    # 只改这一处：`_rerank_external` 读的是模块常量（不是类属性）
    monkeypatch.setattr(pl, "RERANK_REQUEST_TIMEOUT", 0.3)
    a = make_adapter()
    with pytest.raises(Exception, match="超时"):
        _run(a.rerank("q", ["a"]))
    assert a._proc is None, "超时的进程必须丢弃"


# ── 声明与分派 ──

def test_max_documents_exposed(make_adapter):
    """`max_documents` 是**能力声明**，`RerankSearcher` 按它截候选窗口。"""
    a = make_adapter()
    assert a.max_documents == 7


def test_execute_rejects_file_input(make_adapter):
    """重排不吃文件 —— 走 `execute()` 必须显式拒绝（同 `_PluginEmbeddingAdapter`）。"""
    a = make_adapter()
    with pytest.raises(Exception, match="不接受文件"):
        a.execute("x.pdf")


def test_inproc_uses_plugin(make_adapter):
    plugin = _RRPlugin()
    plugin.trusted = True
    plugin.command = []
    plugin.rerank = lambda q, docs: [(i, float(i)) for i in range(len(docs))]
    a = _PluginRerankAdapter(plugin, Path("."), None)
    assert _run(a.rerank("q", ["a", "b"])) == [(0, 0.0), (1, 1.0)]


def test_inproc_bad_return_type_is_explicit(make_adapter):
    plugin = _RRPlugin()
    plugin.trusted = True
    plugin.command = []
    plugin.rerank = lambda q, docs: "不是下标分数对"
    a = _PluginRerankAdapter(plugin, Path("."), None)
    with pytest.raises(Exception, match="list"):
        _run(a.rerank("q", ["a"]))


def _write_rr_plugin(data_dir: Path, name: str = "rr-x") -> Path:
    d = data_dir / "plugins"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.py").write_text(
        "from app.engines.plugin_loader import RerankPlugin\n"
        "\n"
        "class P(RerankPlugin):\n"
        f"    name = {name!r}\n"
        "    model_id = 'm'\n"
        "    max_documents = 5\n"
        "    trusted = True\n"
        "    def rerank(self, query, documents):\n"
        "        return [(i, 0.0) for i in range(len(documents))]\n",
        encoding="utf-8",
    )
    return data_dir


def test_peek_meta_recognizes_rerank_plugin(tmp_path):
    """`RerankPlugin` 子类也能静态读出 name + category。

    漏了这一族的失效形态：插件被**禁用后仍然加载**（peek 解析不出 name → 退回
    「先执行再判」），而那正是「禁用即不 exec 插件代码」这条安全性质。
    """
    _write_rr_plugin(tmp_path)
    meta = _peek_plugin_meta(tmp_path / "plugins" / "rr-x.py")
    assert meta["name"] == "rr-x"
    assert meta["category"] == "rerank", "基类继承来的 category 要补上（否则禁用条目分组错）"


def test_init_plugins_dispatches_rerank_adapter(tmp_path):
    """**最关键的一条**：`category="rerank"` 必须分派到 `_PluginRerankAdapter`。

    不加分派分支的失效形态：走 `_PluginEngineAdapter`（doc 契约）**静默注册成功**
    —— 形状合法、零报错，而每次调用都在要求一个它永远收不到的文件路径。
    """
    reg = dict(PLUGIN_REGISTRY)
    stores = {k: dict(v) for k, v in EngineRegistry._stores.items()}
    try:
        init_plugins({"data_dir": str(_write_rr_plugin(tmp_path))})
        entry = PLUGIN_REGISTRY["rr-x"]
        adapter = entry["adapter"]
        assert isinstance(adapter, _PluginRerankAdapter), \
            f"分了 {type(adapter).__name__}，不是重排适配器"
        assert not isinstance(adapter, pl._PluginEmbeddingAdapter), "分到向量化那一族了"
        assert adapter.max_documents == 5, "适配器没把声明的 max_documents 带出来"
        assert EngineRegistry.get("rerank", "rr-x") is not None, "未注册进 rerank 类别"
    finally:
        PLUGIN_REGISTRY.clear()
        PLUGIN_REGISTRY.update(reg)
        EngineRegistry._stores.clear()
        EngineRegistry._stores.update(stores)


def test_模板与子进程的默认模型目录同源():
    """`rerank_plugin.py` 与 `rerank/server.py` 的两个默认路径必须逐字相同。

    不同源的失效形态是**静默**的：`precheck()` 查 A 目录（说就绪）、子进程读 B
    目录（说缺文件）—— 用户看到的是「设置页说可用，一检索就报缺模型」，而两处
    代码各自都对着。
    """
    def _const(path: Path, name: str) -> str:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign):
                if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                    return ast.literal_eval(node.value)
        raise AssertionError(f"{path.name} 里没有 {name}")

    decl = EXAMPLES / "rerank_plugin.py"
    srv = EXAMPLES / "rerank" / "server.py"
    for name in ("DEFAULT_MODEL_DIR", "DEFAULT_ONNX"):
        assert _const(decl, name) == _const(srv, name), f"{name} 两处不同源"


def test_模板声明了重排族的必备属性():
    """模板是用户照抄的起点 —— 少一个属性就会被加载成「形状合法但不可用」。"""
    src = (EXAMPLES / "rerank_plugin.py").read_text(encoding="utf-8")
    for marker in ('name = "local-bge-reranker"',
                   "model_id = ", "max_documents = ", "is_local = True",
                   'requires_modules = ("onnxruntime", "tokenizers")'):
        assert marker in src, f"模板缺 {marker}"
