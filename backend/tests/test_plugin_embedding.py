"""向量化插件（`EmbeddingPlugin`）— 契约 / 常驻子进程 IPC / 生命周期 / 自检。

判据全部落在**真子进程**上（`sys.executable` 跑一个假 server），不走 mock：
这一层的失败形态几乎都是「协议对了但进程状态错了」（管道缓冲、stderr 写满、
响应错位、进程已死），mock 掉 IO 就什么都测不到。

⚠️ 假 server 的行为由环境变量 `FAKE_MODE` 控制 —— adapter 起子进程时用
`os.environ.copy()`，所以测试进程里设的变量天然传得下去。
"""
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
    _PluginEmbeddingAdapter,
    _PluginEngineAdapter,
    EmbeddingPlugin,
    EnginePlugin,
    PLUGIN_REGISTRY,
    init_plugins,
)

# ── 假 server ──
# 行为矩阵（FAKE_MODE）：ok / wrong_id / error_once / count_mismatch / bad_line /
# slow / noisy_stderr。每条都对应适配器里一条**必须显式失败**的路径。
FAKE_SERVER = r'''
import json, os, sys, time
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

MODE = os.environ.get("FAKE_MODE", "ok")
DIM = int(os.environ.get("FAKE_DIM", "3"))

def emit(o):
    sys.stdout.write(json.dumps(o) + "\n")
    sys.stdout.flush()

# stderr 写满 200KB：adapter 没有 drain 线程的话，子进程会阻塞在这里，ready 永远读不到。
if MODE == "noisy_stderr":
    for _ in range(2000):
        sys.stderr.write("x" * 100 + "\n")
    sys.stderr.flush()

ready = {"ready": True, "dimension": int(os.environ.get("FAKE_READY_DIM", DIM))}
mid = os.environ.get("FAKE_MODEL_ID")
if mid:
    ready["model_id"] = mid
emit(ready)

served = 0
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    rid = req.get("id")
    texts = req.get("texts") or []
    # 每次请求留一个痕迹（「空输入不发起调用」的判据载体）
    if os.environ.get("FAKE_CALL_LOG"):
        with open(os.environ["FAKE_CALL_LOG"], "a", encoding="utf-8") as f:
            f.write(str(len(texts)) + "\n")
    if MODE == "wrong_id":
        emit({"id": (rid or 0) + 100, "vectors": []})
        continue
    if MODE == "error_once" and served == 0:
        served += 1
        emit({"id": rid, "error": "boom"})
        continue
    served += 1
    if MODE == "count_mismatch":
        emit({"id": rid, "vectors": [[0.0] * DIM]})
        continue
    if MODE == "bad_line":
        sys.stdout.write("not json\n")
        sys.stdout.flush()
        continue
    if MODE == "slow":
        time.sleep(3)
    # 每条文本 → 一个可辨认的向量（首元素是文本长度），用来验顺序
    vecs = []
    for t in texts:
        v = [float(len(t))] + [0.0] * (DIM - 1)
        vecs.append(v[:DIM])
    emit({"id": rid, "vectors": vecs})
'''


class _EmbPlugin(EmbeddingPlugin):
    name = "test-emb"
    command = []                     # 每个用例改到实例上
    model_id = "test-emb-model"
    dimension = 3
    display_name = "测试向量插件"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """每个用例从干净的环境变量开始（FAKE_* 会漏进子进程）。"""
    for k in ("FAKE_MODE", "FAKE_DIM", "FAKE_READY_DIM", "FAKE_MODEL_ID", "FAKE_CALL_LOG"):
        monkeypatch.delenv(k, raising=False)
    yield


@pytest.fixture
def make_adapter(tmp_path):
    """造一个 adapter，并在用例结束后**确保杀掉子进程**（残留会拖慢整个测试会话）。"""
    script = tmp_path / "fake_server.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    made = []

    def _make(**attrs):
        plugin = _EmbPlugin()
        plugin.command = [sys.executable, "-u", str(script)]
        for k, v in attrs.items():
            setattr(plugin, k, v)
        adapter = _PluginEmbeddingAdapter(plugin, tmp_path, script)
        made.append(adapter)
        return adapter

    yield _make
    for a in made:
        a.release(after_idle=0)


def _run(coro):
    return asyncio.run(coro)


# ── 契约 ──

def test_embed_keeps_order_and_shape(make_adapter):
    a = make_adapter()
    texts = ["a", "bbbb", "cc"]
    out = _run(a.embed(texts))
    assert [v[0] for v in out] == [1.0, 4.0, 2.0], "向量与文本必须一一对应（顺序不得错位）"
    assert all(len(v) == 3 for v in out)


def test_embed_empty_returns_empty_without_calling(tmp_path, make_adapter, monkeypatch):
    """契约 3：空输入返回空，且**不发起任何调用**（连子进程都不该起）。"""
    log = tmp_path / "calls.log"
    monkeypatch.setenv("FAKE_CALL_LOG", str(log))
    a = make_adapter()
    assert _run(a.embed([])) == []
    assert not log.exists(), "空输入仍然发起了下游调用"
    assert a._proc is None, "空输入把子进程拉起来了（白付 3–5 秒加载）"


def test_embed_count_mismatch_raises(make_adapter, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "count_mismatch")
    a = make_adapter()
    with pytest.raises(RuntimeError, match="条数不符"):
        _run(a.embed(["a", "b"]))


def test_embed_dimension_mismatch_raises(make_adapter):
    a = make_adapter()
    a.dimension = 4          # 声明 4 维，server 回 3 维
    with pytest.raises(RuntimeError, match="维度"):
        _run(a.embed(["a"]))


# ── 进程生命周期 ──

def test_warmup_spawns_once_and_reuses(make_adapter):
    a = make_adapter()
    _run(a.warmup())
    pid = a._proc.pid
    _run(a.embed(["x"]))
    assert a._proc.pid == pid, "第二次调用重新起了进程（模型被重复加载）"


def test_release_after_idle_zero_kills(make_adapter):
    a = make_adapter()
    _run(a.warmup())
    assert a._proc is not None
    assert a.release(after_idle=0) is True
    assert a._proc is None
    assert a.release(after_idle=0) is False, "没有进程可杀时应返回 False"


def test_release_respects_idle_threshold(make_adapter):
    a = make_adapter()
    _run(a.warmup())
    assert a.release(after_idle=600) is False, "刚用过就被判闲置 —— 会把模型踢掉"
    assert a._proc is not None


def test_proc_restarts_after_external_death(make_adapter):
    """子进程被外力杀掉（OOM / 用户任务管理器）→ 下一次调用自动重起。"""
    a = make_adapter()
    _run(a.warmup())
    a._proc.kill()
    a._proc.wait(timeout=5)
    out = _run(a.embed(["ok"]))
    assert out[0][0] == 2.0


def test_stderr_flood_does_not_block_ready(make_adapter, monkeypatch):
    """stderr 写满管道（Windows 约 64KB）时不 drain 的话，子进程卡在写日志上，
    ready 永远读不到 —— 现象是「启动超时」，而真正的原因在没人读的那段里。"""
    monkeypatch.setenv("FAKE_MODE", "noisy_stderr")
    a = make_adapter()
    _run(a.warmup())
    assert a._proc is not None


# ── 协议违例一律显式失败 ──

def test_wrong_id_kills_proc(make_adapter, monkeypatch):
    """响应 id 对不上 = 管道错位。必须丢弃进程，否则每一轮都读到上一条的结果。"""
    monkeypatch.setenv("FAKE_MODE", "wrong_id")
    a = make_adapter()
    with pytest.raises(RuntimeError, match="响应错位"):
        _run(a.embed(["a"]))
    assert a._proc is None, "错位后必须丢弃进程"


def test_non_json_line_kills_proc(make_adapter, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "bad_line")
    a = make_adapter()
    with pytest.raises(RuntimeError, match="非 JSON"):
        _run(a.embed(["a"]))
    assert a._proc is None


def test_error_response_keeps_proc(make_adapter, monkeypatch):
    """单次编码失败 ≠ 协议坏了：进程留着，下一批继续用。"""
    monkeypatch.setenv("FAKE_MODE", "error_once")
    a = make_adapter()
    with pytest.raises(RuntimeError, match="编码失败"):
        _run(a.embed(["a"]))
    assert a._proc is not None
    assert _run(a.embed(["b"]))[0][0] == 1.0


def test_request_timeout_kills_proc(make_adapter, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "slow")
    monkeypatch.setattr(pl, "EMBED_REQUEST_TIMEOUT", 0.5)
    a = make_adapter()
    with pytest.raises(RuntimeError, match="超时"):
        _run(a.embed(["a"]))
    assert a._proc is None


def test_ready_dimension_mismatch_fails_spawn(make_adapter, monkeypatch):
    """声明值与实际值不符必须当场报：写进库之后口径不符是静默的。"""
    monkeypatch.setenv("FAKE_READY_DIM", "8")
    a = make_adapter()
    with pytest.raises(RuntimeError, match="维度"):
        _run(a.warmup())
    assert a._proc is None


def test_ready_model_mismatch_fails_spawn(make_adapter, monkeypatch):
    monkeypatch.setenv("FAKE_MODEL_ID", "someone-else")
    a = make_adapter()
    with pytest.raises(RuntimeError, match="模型"):
        _run(a.warmup())


def test_spawn_failure_reports_stderr(make_adapter, tmp_path):
    """解释器 / 脚本不存在 → 起不来，错误信息要能看出是哪一步。"""
    a = make_adapter()
    a._plugin.command = [str(tmp_path / "nope" / "python.exe"), "-u", "x.py"]
    with pytest.raises(Exception):
        _run(a.embed(["a"]))


# ── 配置传递 ──

def test_plugin_config_reaches_subprocess(make_adapter, tmp_path):
    """配置经 `LIMO_PLUGIN_CONFIG` 传给子进程，且改了立刻生效（下次起进程即新值）。"""
    see = tmp_path / "seen.json"
    script = tmp_path / "cfg_server.py"
    script.write_text(
        "import json, os, sys\n"
        "sys.stdout.reconfigure(encoding='utf-8')\n"
        f"open({str(see)!r}, 'w', encoding='utf-8').write("
        "os.environ.get('LIMO_PLUGIN_CONFIG', ''))\n"
        "sys.stdout.write(json.dumps({'ready': True, 'dimension': 2}) + '\\n')\n"
        "sys.stdout.flush()\n"
        "for line in sys.stdin: pass\n",
        encoding="utf-8",
    )
    plugin = _EmbPlugin()
    plugin.command = [sys.executable, "-u", str(script)]
    plugin.dimension = 2
    a = _PluginEmbeddingAdapter(plugin, tmp_path, script)
    try:
        a.plugin_config = {"model_dir": "/x/y"}
        assert plugin.plugin_config == {"model_dir": "/x/y"}, "配置没同步给插件实例"
        _run(a.warmup())
        assert json.loads(see.read_text(encoding="utf-8")) == {"model_dir": "/x/y"}
    finally:
        a.release(after_idle=0)


# ── 自检 ──

def test_precheck_rejects_zero_dimension(make_adapter):
    a = make_adapter()
    a.dimension = 0
    assert "dimension" in a.precheck()


def test_precheck_reports_missing_plugin_files(tmp_path):
    """`requires_files` 里写死的必需文件不存在 → 给用户看的原因。"""
    plugin = _EmbPlugin()
    plugin.command = [sys.executable, "-u", str(tmp_path / "nope.py")]
    plugin.requires_files = ("tokenizer.json",)
    a = _PluginEmbeddingAdapter(plugin, tmp_path, None)
    reason = a.precheck()
    assert reason and "tokenizer.json" in reason


def test_precheck_reports_missing_venv_deps(tmp_path):
    """`requires_modules` → `probe_venv` 判子 venv（不是 `probe`：依赖不在主进程里）。"""
    plugin = _EmbPlugin()
    plugin.command = ["sub/.venv/Scripts/python.exe", "-u", "sub/server.py"]
    plugin.requires_modules = ("onnxruntime",)
    a = _PluginEmbeddingAdapter(plugin, tmp_path, None)
    reason = a.precheck()
    assert reason and "venv" in reason.lower()


def test_precheck_forwards_plugin_precheck():
    plugin = _EmbPlugin()
    a = _PluginEmbeddingAdapter(plugin, Path("."), None)
    plugin.precheck = lambda: "模型没下载"
    assert a.precheck() == "模型没下载"
    plugin.precheck = lambda: (_ for _ in ()).throw(RuntimeError("炸了"))
    assert "自检失败" in a.precheck()


# ── 进程内（trusted）路径 ──

def test_inproc_embed_uses_plugin(make_adapter):
    plugin = _EmbPlugin()
    plugin.trusted = True
    plugin.command = []
    plugin.embed = lambda texts: [[float(len(t))] * 3 for t in texts]
    a = _PluginEmbeddingAdapter(plugin, Path("."), None)
    assert a._exec_mode == "inproc"
    out = _run(a.embed(["ab"]))
    assert out == [[2.0, 2.0, 2.0]]


def test_inproc_bad_return_type_is_explicit(make_adapter):
    plugin = _EmbPlugin()
    plugin.trusted = True
    plugin.command = []
    plugin.embed = lambda texts: "不是向量"
    a = _PluginEmbeddingAdapter(plugin, Path("."), None)
    with pytest.raises(Exception, match="list"):
        _run(a.embed(["a"]))


# ── 与文件引擎的边界 ──

def test_execute_rejects_file_input(make_adapter):
    a = make_adapter()
    with pytest.raises(Exception, match="不接受文件"):
        a.execute("x.pdf")


# ── 静态解析与加载分派 ──

def _write_emb_plugin(data_dir: Path, name: str = "emb-x") -> Path:
    d = data_dir / "plugins"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.py").write_text(
        "from app.engines.plugin_loader import EmbeddingPlugin\n"
        "\n"
        "class P(EmbeddingPlugin):\n"
        f"    name = {name!r}\n"
        "    model_id = 'm'\n"
        "    dimension = 8\n"
        "    trusted = True\n"
        "    def embed(self, texts):\n"
        "        return [[0.0] * 8 for _ in texts]\n",
        encoding="utf-8",
    )
    return data_dir


def test_peek_meta_recognizes_embedding_plugin(tmp_path):
    """`EmbeddingPlugin` 子类也能静态读出 name（否则「禁用即不加载」对它失效）。"""
    _write_emb_plugin(tmp_path)
    meta = _peek_plugin_meta(tmp_path / "plugins" / "emb-x.py")
    assert meta["name"] == "emb-x"
    assert meta["category"] == "embedding", "基类继承来的 category 要补上（否则禁用条目分组错）"


def test_init_plugins_dispatches_embedding_adapter(tmp_path):
    reg = dict(PLUGIN_REGISTRY)
    stores = {k: dict(v) for k, v in EngineRegistry._stores.items()}
    try:
        init_plugins({"data_dir": str(_write_emb_plugin(tmp_path))})
        entry = PLUGIN_REGISTRY["emb-x"]
        assert isinstance(entry["adapter"], _PluginEmbeddingAdapter)
        assert not isinstance(entry["adapter"], type(None))
        assert EngineRegistry.get("embedding", "emb-x") is not None, "未注册进 embedding 类别"
    finally:
        PLUGIN_REGISTRY.clear()
        PLUGIN_REGISTRY.update(reg)
        EngineRegistry._stores.clear()
        EngineRegistry._stores.update(stores)


def test_init_plugins_skips_base_class_import(tmp_path):
    """声明文件 `from ... import EmbeddingPlugin` 会把基类带进模块命名空间 ——
    基类本身也是 `EnginePlugin` 子类，不排除就会被当成插件实例化。"""
    reg = dict(PLUGIN_REGISTRY)
    stores = {k: dict(v) for k, v in EngineRegistry._stores.items()}
    try:
        init_plugins({"data_dir": str(_write_emb_plugin(tmp_path, name="emb-y"))})
        assert "" not in PLUGIN_REGISTRY, "基类被当成插件实例化了（幽灵条目 name=''）"
    finally:
        PLUGIN_REGISTRY.clear()
        PLUGIN_REGISTRY.update(reg)
        EngineRegistry._stores.clear()
        EngineRegistry._stores.update(stores)


# ── 本轮审查修复的守卫 ──

def test_idle_release_threshold_matches_builtin():
    """两份「闲置多久算久」必须相等。

    `idle` 档由**引擎自己**判门槛（worker 刻意不传数字，见 `maybe_release` 的文档）：
    内置引擎读 `embedding_local.DEFAULT_IDLE_RELEASE`，插件适配器读
    `plugin_loader.EMBED_IDLE_RELEASE`。分叉时**什么都不报** —— 用户换了个引擎，
    「闲置后释放」的节奏就变了，而设置页那三档文案说的是同一件事。
    """
    from app.engines.embedding_local import DEFAULT_IDLE_RELEASE
    assert pl.EMBED_IDLE_RELEASE == DEFAULT_IDLE_RELEASE


def test_plugin_env_forces_utf8():
    """子进程环境必须带 `PYTHONUTF8=1`。

    缺了它，Windows 上子进程 stdio 是 GBK + `surrogateescape`：主进程写进去的 UTF-8
    被解成代理字符而**不抛异常**，`json.loads` 照样成功 —— 编码出来的是乱码文本的
    向量，检索质量下降、日志全绿。stdout 那一侧有 `encoding="utf-8"` 管着，stdin 没有。
    """
    assert pl._plugin_env()["PYTHONUTF8"] == "1"


def test_release_gives_up_when_encoding_in_flight(make_adapter, monkeypatch):
    """锁被一次编码往返占着时，`release` 立刻返回 False 而不是把调用方挂住。

    消费方 `vector_runtime._stop_worker` 跑在**事件循环线程**上（用户点「关掉语义
    检索」的那条路径），挂住 = 整个后端停摆到那次往返结束（上限 180 秒），顶栏弹
    「正在重试」而原因不是后端死了。
    """
    a = make_adapter()
    monkeypatch.setattr(pl, "RELEASE_LOCK_TIMEOUT", 0.05)
    assert a._proc_lock.acquire(blocking=False), "前置：锁空闲"
    try:
        t0 = time.monotonic()
        assert a.release(after_idle=0) is False
        assert time.monotonic() - t0 < 1.0, "抢锁失败必须是「马上说不」"
    finally:
        a._proc_lock.release()


def test_inproc_release_forwards_plugin_hook():
    """进程内插件实现了 `release` 就必须被调到（与 `warmup` 成对）。

    不转发时 `EmbeddingPlugin` 的钩子写了也永不被调用，而**外部模式那份工作正常** ——
    插件作者换个形态发布才发现「选了常驻内存就永远不还」。
    """
    plugin = _EmbPlugin()
    plugin.trusted = True
    plugin.command = []
    calls = []

    def _release(*, after_idle=600.0):
        calls.append(after_idle)
        return True

    plugin.release = _release
    a = _PluginEmbeddingAdapter(plugin, Path("."), None)
    assert a.release(after_idle=0) is True
    assert calls == [0]


def test_inproc_release_without_hook_is_noop():
    """没实现钩子 = 无可释放（同 `warmup` / `precheck` 的 `getattr` 取舍）。"""
    plugin = _EmbPlugin()
    plugin.trusted = True
    plugin.command = []
    a = _PluginEmbeddingAdapter(plugin, Path("."), None)
    assert a.release(after_idle=0) is False


def test_ready_with_non_integer_dimension_is_reported_clearly(tmp_path):
    """`dimension` 不是整数时必须是「报告值不对」，不是裸 `ValueError`。

    裸 `invalid literal for int()` 会把「插件写错了协议」显示成主程序内部的错误。
    """
    script = tmp_path / "bad_dim.py"
    script.write_text(
        "import json, sys\n"
        "sys.stdin.reconfigure(encoding='utf-8')\n"
        "sys.stdout.reconfigure(encoding='utf-8')\n"
        "print(json.dumps({'ready': True, 'dimension': 'abc'}), flush=True)\n"
        "sys.stdin.read()\n",
        encoding="utf-8",
    )
    plugin = _EmbPlugin()
    plugin.command = [sys.executable, "-u", str(script)]
    a = _PluginEmbeddingAdapter(plugin, tmp_path, script)
    with pytest.raises(RuntimeError, match="不是整数"):
        a._embed_external(["x"])
