"""插件加载器单测 — 门控 / 分派 / stdout 回退 / 禁用即不加载 / 配置回填"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from pathlib import Path

import pytest

from app.core.registry import EngineRegistry
from app.engines.plugin_loader import (
    _parse_stdout,
    _dispatch_result,
    _resolve_exec_mode,
    _peek_plugin_meta,
    init_plugins,
    enumerate_plugins,
    set_plugin_config,
    set_plugin_enabled,
    EnginePlugin,
    PLUGIN_REGISTRY,
    PLUGIN_API_VERSION,
)


class _DocPlugin(EnginePlugin):
    name = "test-doc"
    category = "doc"
    command = ["python", "engine.py"]


class _OCRPlugin(EnginePlugin):
    name = "test-ocr"
    category = "ocr"
    command = ["python", "engine.py"]


class _ASRPlugin(EnginePlugin):
    name = "test-asr"
    category = "asr"
    command = ["python", "engine.py"]


class _TrustedPlugin(EnginePlugin):
    name = "test-trusted"
    category = "doc"
    trusted = True


class _NoModePlugin(EnginePlugin):
    name = "test-nomode"
    category = "doc"


# ── 门控 ──

def test_resolve_exec_mode_external():
    assert _resolve_exec_mode(_DocPlugin()) == "external"


def test_resolve_exec_mode_trusted():
    assert _resolve_exec_mode(_TrustedPlugin()) == "inproc"


def test_resolve_exec_mode_rejected():
    assert _resolve_exec_mode(_NoModePlugin()) is None


# ── stdout 解析（JSON 优先 + v0 回退）──

def test_parse_stdout_json():
    assert _parse_stdout('{"text": "hi", "pages": 3}') == {"text": "hi", "pages": 3}


def test_parse_stdout_v0_plain():
    p = _parse_stdout("plain markdown output")
    assert p["text"] == "plain markdown output"
    assert p["_v0"] is True


def test_parse_stdout_v0_broken_json():
    p = _parse_stdout('{"text": broken')
    assert p["text"] == '{"text": broken'
    assert p["_v0"] is True


def test_parse_stdout_whitespace():
    p = _parse_stdout("  \n some md \n")
    assert p["text"] == "some md"


# ── 分派 ──

def test_dispatch_doc():
    from app.engines.doc_types import DocResult
    r = _dispatch_result("doc", {"text": "md", "pages": 5}, "p")
    assert isinstance(r, DocResult)
    assert r.text == "md" and r.pages == 5
    assert r.is_scanned is False


def test_dispatch_doc_defaults():
    from app.engines.doc_types import DocResult
    r = _dispatch_result("doc", {"text": "md"}, "p")
    assert isinstance(r, DocResult)
    assert r.pages == 1
    assert r.is_scanned is False and r.scanned_pages == 0


def test_dispatch_ocr():
    from app.engines.ocr import OCRResult
    r = _dispatch_result("ocr", {"text": "t", "confidence": 0.9}, "p")
    assert isinstance(r, OCRResult)
    assert r.text == "t" and r.confidence == 0.9


def test_dispatch_asr_segments():
    from app.engines.transcript import TranscriptResult
    r = _dispatch_result("asr", {
        "segments": [{"start": 0, "end": 1.5, "text": "a"}],
        "language": "zh",
    }, "p")
    assert isinstance(r, TranscriptResult)
    assert r.segments[0].text == "a"
    assert r.segments[0].end == 1.5
    assert r.language == "zh"
    assert r.full_text == "a"


def test_dispatch_asr_v0_plain():
    from app.engines.transcript import TranscriptResult
    r = _dispatch_result("asr", {"text": "single line"}, "p")
    assert isinstance(r, TranscriptResult)
    assert len(r.segments) == 1
    assert r.segments[0].text == "single line"


def test_dispatch_unknown_category_doc_contract():
    from app.engines.doc_types import DocResult
    r = _dispatch_result("future-category", {"text": "x"}, "p")
    assert isinstance(r, DocResult)


# ── 契约版本 ──

def test_api_version():
    assert PLUGIN_API_VERSION == 1


# ══════════════════════════════════════════════════════════════════════
# P1-02：禁用即不加载（`exec_module` 一次都不发生）+ 落盘配置回填
# ══════════════════════════════════════════════════════════════════════
#
# 修复前的两件事：
#   ① `plugin_states[name] is False` 只免注册、**代码照跑** —— 而插件是任意 Python，
#      `exec_module` 那一刻它就能联网/写盘（与主程序同权限，`docs/plugins.md` 有告示），
#      与前端「重启后停用」的文案不符。修法：先从源码静态读名字，命中禁用就整个跳过。
#   ② `init_plugins` 从不读 `config["plugin_configs"]` —— 只有 `PUT /plugins/{name}/config`
#      写内存，于是重启后插件拿到的恒是空 dict，用户保存的配置静默丢失（UI 上那条还在）。
#
# ⚠️ 本段用完 `init_plugins`，它会写两个**模块级全局**（`PLUGIN_REGISTRY` 与
# `EngineRegistry._stores`，后者没有 unregister API）→ 必须逐用例拍快照恢复。

SIDE_EFFECT = "SIDE_EFFECT.txt"


@pytest.fixture(autouse=True)
def _isolate_plugin_globals():
    """`init_plugins` 的全局副作用恢复（否则第一条用例留下的插件会污染后一条）。"""
    reg = dict(PLUGIN_REGISTRY)
    stores = {k: dict(v) for k, v in EngineRegistry._stores.items()}
    yield
    PLUGIN_REGISTRY.clear()
    PLUGIN_REGISTRY.update(reg)
    EngineRegistry._stores.clear()
    EngineRegistry._stores.update(stores)


def _write_plugin(data_dir: Path, name: str, *, category: str = "doc",
                  side_effect: bool = False, name_expr: str | None = None) -> Path:
    """往 `{data_dir}/plugins/` 写一个最小插件声明文件，返回数据目录。

    `side_effect=True` 时顶层写一个标记文件 —— 它同时是「禁用即不加载」的**判据载体**：
    代码被执行过就一定会留下这个文件（照 `docs/code-audit-2.md` 里 P1-02 的验证办法）。
    `name_expr` 用来写非字面量的 name（验证静态解析撑不住时的安全降级）。
    """
    d = data_dir / "plugins"
    d.mkdir(parents=True, exist_ok=True)
    lines = []
    if side_effect:
        lines += [
            "import pathlib",
            f'pathlib.Path(__file__).with_name("{SIDE_EFFECT}")'
            '.write_text("executed", encoding="utf-8")',
            "",
        ]
    lines += [
        "from app.engines.plugin_loader import EnginePlugin",
        "",
        "",
        "class P(EnginePlugin):",
        f"    name = {name_expr or repr(name)}",
        f"    category = {category!r}",
        '    command = ["python", "engine.py"]',
        "",
    ]
    (d / f"{name}.py").write_text("\n".join(lines), encoding="utf-8")
    return data_dir


# ── 静态解析（纯函数）──

def test_peek_plugin_meta_reads_string_literals(tmp_path):
    """从源码读出声明类上的字符串字面量属性（不执行代码）。"""
    _write_plugin(tmp_path, "peek-a", category="ocr")
    meta = _peek_plugin_meta(tmp_path / "plugins" / "peek-a.py")
    assert meta == {"name": "peek-a", "category": "ocr"}, meta


def test_peek_plugin_meta_dynamic_name_returns_none(tmp_path):
    """动态拼 name 读不出来 → None（调用方退回「执行后再判」，不是静默漏装）。"""
    _write_plugin(tmp_path, "peek-dyn", name_expr='"peek-" + "dyn"')
    assert _peek_plugin_meta(tmp_path / "plugins" / "peek-dyn.py") is None


def test_peek_plugin_meta_ignores_non_plugin_class(tmp_path):
    """不是 `EnginePlugin` 子类的类，即使有 name 也不采信。"""
    d = tmp_path / "plugins"
    d.mkdir(parents=True)
    (d / "notplugin.py").write_text(
        'class NotAPlugin:\n    name = "nope"\n    category = "doc"\n', encoding="utf-8")
    assert _peek_plugin_meta(d / "notplugin.py") is None


def test_peek_plugin_meta_broken_source_returns_none(tmp_path):
    """语法错误的文件返回 None 而非抛异常 —— 它在「加载前」跑，不该炸掉加载器。"""
    d = tmp_path / "plugins"
    d.mkdir(parents=True)
    (d / "broken.py").write_text("class P(EnginePlugin:\n", encoding="utf-8")
    assert _peek_plugin_meta(d / "broken.py") is None


# ── 主判据：禁用即不加载 ──

def test_disabled_plugin_code_never_executed(tmp_path):
    """`plugin_states[name]=False` → 顶层代码**一次都不执行**（P1-02 主判据）。

    ⚠️ 同一用例里跑**同一份文件**的对照组（配置里去掉落禁用 = 默认启用）：「标记文件
    没出现」既可能是「跳过了」也可能是「压根没扫到这个文件」，只有把同一份文件放进启用
    配置再跑一遍，才能证明这条断言看的是**配置**而不是扫描（模式 #38）。
    """
    data = _write_plugin(tmp_path, "p102-off", side_effect=True)
    mark = data / "plugins" / SIDE_EFFECT

    init_plugins({"data_dir": str(data), "plugin_states": {"p102-off": False}})

    assert not mark.exists(), "被禁用的插件，其顶层代码仍被执行了（「禁用」只免了注册）"
    entry = PLUGIN_REGISTRY["p102-off"]
    assert entry["adapter"] is None, "禁用路径不应实例化插件（不需要它的任何行为）"
    assert entry["enabled"] is False and entry["loaded"] is False
    assert entry["reason"] == "disabled-by-config"
    assert entry["category"] == "doc", "静态读出的 category 应带到占位条目上"
    assert EngineRegistry.get("doc", "p102-off") is None, "禁用的插件不得进引擎注册表"

    # 对照组：同一份文件，换成默认启用 → 标记文件必须出现
    init_plugins({"data_dir": str(data)})
    assert mark.exists(), "对照组失败：启用配置下也没执行 → 上面的断言没有区分力"
    assert EngineRegistry.get("doc", "p102-off") is not None


def test_dynamic_name_plugin_falls_back_to_execute_then_check(tmp_path):
    """name 静态读不出来 → 退回旧行为（先执行、再判 enabled），但仍不注册为引擎。

    ⚠️ 这条钉的是一个**已知代价**而非理想行为：静态解析撑不住所有写法，而「误执行一个
    本该禁用的插件」比「漏装一个插件」轻，降级方向刻意选安全那侧。写下来是为了别让后人
    以为「禁用 == 一定不执行」—— 那种保证只有字面量 name 的插件才有。
    """
    data = _write_plugin(tmp_path, "p102-dyn", side_effect=True,
                         name_expr='"p102-" + "dyn"')
    init_plugins({"data_dir": str(data), "plugin_states": {"p102-dyn": False}})

    assert (data / "plugins" / SIDE_EFFECT).exists(), "静态读不出 name 时应退化为「执行后再判」"
    entry = PLUGIN_REGISTRY["p102-dyn"]
    assert entry["enabled"] is False and entry["loaded"] is False
    assert EngineRegistry.get("doc", "p102-dyn") is None, "降级路径仍不得注册为引擎"


# ── 配置回填 ──

def test_plugin_config_restored_on_startup(tmp_path):
    """重启加载时回填 `plugin_configs`；没配过的插件拿空 dict（键缺失不是错误）。"""
    data = _write_plugin(tmp_path, "p102-conf")
    _write_plugin(tmp_path, "p102-noconf")

    init_plugins({
        "data_dir": str(data),
        "plugin_configs": {"p102-conf": {"depth": "3", "mode": "fast"}},
    })

    assert PLUGIN_REGISTRY["p102-conf"]["adapter"].plugin_config == \
        {"depth": "3", "mode": "fast"}, "落盘配置未回填 → 重启后插件退回默认值"
    assert PLUGIN_REGISTRY["p102-noconf"]["adapter"].plugin_config == {}


# ── 禁用插件的管理 API 不能崩 ──

def test_enumerate_survives_disabled_plugin(tmp_path):
    """禁用插件没有 adapter → `enumerate_plugins` 不得崩，且仍能点「启用」。

    ⚠️ 这条**不是本轮修复的区分力证据**（旧实现在这里也有 adapter），它守的是**新
    守卫**：新实现把 adapter 置 None，没有那段占位分支就是 AttributeError（插件页整页白）。
    """
    data = _write_plugin(tmp_path, "p102-enum", category="ocr")
    init_plugins({"data_dir": str(data), "plugin_states": {"p102-enum": False}})

    items = {p["name"]: p for p in enumerate_plugins()}
    assert "p102-enum" in items, "被禁用的插件仍应出现在列表里（否则用户无法重新启用）"
    it = items["p102-enum"]
    assert it["enabled"] is False and it["loaded"] is False
    assert it["pendingRestart"] is False
    assert it["category"] == "ocr", "category 来自静态解析，不该因不加载而丢"
    assert it["reason"] == "disabled-by-config"
    assert it["config"] == {}

    # 点「启用」→ 立即反映期望态；loaded 仍 False（引擎注册要重启才生效）
    set_plugin_enabled("p102-enum", True)
    it2 = {p["name"]: p for p in enumerate_plugins()}["p102-enum"]
    assert it2["enabled"] is True and it2["loaded"] is False
    assert it2["pendingRestart"] is True, "启用后应提示「重启生效」"


def test_set_plugin_config_on_disabled_plugin_is_noop(tmp_path):
    """无 adapter 时 setter 不得抛：配置已由 router 落盘，重启加载时回填。"""
    data = _write_plugin(tmp_path, "p102-setter")
    init_plugins({"data_dir": str(data), "plugin_states": {"p102-setter": False}})

    set_plugin_config("p102-setter", {"k": "v"})   # 不该抛

    assert PLUGIN_REGISTRY["p102-setter"]["adapter"] is None
