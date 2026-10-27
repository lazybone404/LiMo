"""插件加载器单测 — 门控 / 分派 / stdout 回退 / 禁用即不加载 / 配置回填"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from pathlib import Path

import pytest

from app.core.registry import EngineRegistry
from app.core.errors import FatalError
from app.engines.plugin_loader import (
    _parse_stdout,
    _dispatch_result,
    _resolve_exec_mode,
    _peek_plugin_meta,
    _plugin_subdir,
    _PluginEngineAdapter,
    init_plugins,
    enumerate_plugins,
    install_plugin_deps,
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


# ══════════════════════════════════════════════════════════════════════
# 就绪自检（`EnginePlugin.precheck` → `enumerate_plugins.ready`）
# ══════════════════════════════════════════════════════════════════════
#
# 这条契约存在的理由：插件可能装着、加载着，但**用不了**（缺 tesseract 二进制、
# 缺模型文件、缺 API Key）。没有自检的话，用户会「启用 → 选它 → 点生成」走完三步
# 才发现不行，代价是一次失败任务；有自检则是设置页上一句话。
#
# ⚠️ 三态刻意分开：`ready=true`（就绪）/ `false`（自检说不可用）/ `null`（**无法判断**
# —— 插件被禁用、代码没加载，自检根本没机会跑）。把 null 折成 false 会让每个被禁用的
# 插件都挂一条点不动的「不可用」。

def _write_precheck_plugin(data_dir: Path, name: str, body: str) -> Path:
    """写一个实现了 `precheck()` 的插件声明文件（`body` 是方法体，已含缩进前的原文）。"""
    d = data_dir / "plugins"
    d.mkdir(parents=True, exist_ok=True)
    src = (
        "from app.engines.plugin_loader import EnginePlugin\n\n\n"
        "class P(EnginePlugin):\n"
        f"    name = {name!r}\n"
        "    category = 'doc'\n"
        '    command = ["python", "engine.py"]\n\n'
        "    def precheck(self):\n"
        + "".join(f"        {ln}\n" for ln in body.splitlines())
    )
    (d / f"{name}.py").write_text(src, encoding="utf-8")
    return data_dir


def test_precheck_默认实现返回就绪():
    """不重写 `precheck` 的插件 = 「装上了就能用」，不必写空方法。

    ⚠️ 默认值必须是 `None`（就绪）而不是 `"未实现"`：后者会把所有存量插件一次性
    标成不可用，而它们本来跑得好好的。
    """
    assert EnginePlugin().precheck() is None
    assert _DocPlugin().precheck() is None


def test_enumerate_自检通过时_ready_为_真(tmp_path):
    data = _write_precheck_plugin(tmp_path, "pc-ok", "return None")
    init_plugins({"data_dir": str(data)})

    it = {p["name"]: p for p in enumerate_plugins()}["pc-ok"]
    assert it["ready"] is True
    assert it["precheckReason"] == ""


def test_enumerate_自检不通过时带出原因(tmp_path):
    """`ready=false` + **可直接展示的原因** —— 前端那一行渲染的就是它。"""
    data = _write_precheck_plugin(tmp_path, "pc-bad", 'return "缺少 tesseract 二进制"')
    init_plugins({"data_dir": str(data)})

    it = {p["name"]: p for p in enumerate_plugins()}["pc-bad"]
    assert it["ready"] is False
    assert "缺少 tesseract 二进制" in it["precheckReason"]


def test_自检自己炸了不连累插件列表(tmp_path):
    """插件是**任意第三方 Python**，它的 `precheck` 抛异常必须在这里被兜住。

    没有这一层兜底，症状是「打开插件设置页整页 500」—— 看起来像主程序坏了，
    而真正的原因在一个用户自己装的插件里。
    """
    data = _write_precheck_plugin(tmp_path, "pc-boom", 'raise RuntimeError("boom")')
    init_plugins({"data_dir": str(data)})

    it = {p["name"]: p for p in enumerate_plugins()}["pc-boom"]
    assert it["ready"] is False
    assert "插件自检失败" in it["precheckReason"]
    assert "boom" in it["precheckReason"], "原始信息要带出来，否则无从排查"


def test_禁用插件的_ready_是_null_不是_false(tmp_path):
    """`null` = 无法判断（代码没加载）。

    ⚠️ 反向断言（`is not False`）是本条的全部意义：改成 `False` 后列表照样渲染、
    所有断言看起来都成立，只是每个被禁用的插件上多了一条点不动的「不可用」。
    """
    data = _write_plugin(tmp_path, "pc-off")
    init_plugins({"data_dir": str(data), "plugin_states": {"pc-off": False}})

    it = {p["name"]: p for p in enumerate_plugins()}["pc-off"]
    assert it["ready"] is None
    assert it["ready"] is not False
    assert it["precheckReason"] == ""


# ── 子目录解析（「一键安装依赖」找 requirements.txt 的地方）──
#
# ⚠️ 这一族守的失效形态**完全没有征兆**：子目录算错时，`requirements.txt` 找不到 →
# `install-deps` 返回 `no-requirements` → 前端**根本不渲染安装按钮**。插件本身照样
# 能加载（command 是对的），于是整件事看起来就是「这个插件不需要依赖」。
# 此前三个官方模板全中这一条。

class _CmdPlugin(EnginePlugin):
    """`command` 可配的最小插件（其余字段随便，本段只关心子目录推导）。"""

    def __init__(self, command):
        self.name = "cmd-plugin"
        self.category = "doc"
        self.command = command


def _adapter(tmp_path: Path, command, source_name: str = "cmd_plugin.py"):
    src = tmp_path / source_name
    src.write_text("", encoding="utf-8")
    return _PluginEngineAdapter(_CmdPlugin(command), tmp_path, src)


def test_subdir_从_command_推导而不是_stem(tmp_path):
    """`pymupdf4llm_plugin.py` + `pymupdf4llm/` —— 官方模板的真实形态。

    stem 推导会得到 `pymupdf4llm_plugin/`，而那个目录**不存在**。
    """
    a = _adapter(tmp_path, ["pymupdf4llm/.venv/Scripts/python.exe", "pymupdf4llm/engine.py"])
    assert _plugin_subdir(a) == tmp_path / "pymupdf4llm"


def test_subdir_跳过_venv_段(tmp_path):
    """第一个相对路径参数是解释器，它的 parent 是 `.venv/Scripts` —— 不是插件目录。"""
    a = _adapter(tmp_path, ["pkg/.venv/Scripts/python.exe", "pkg/engine.py"])
    assert _plugin_subdir(a) == tmp_path / "pkg"


def test_subdir_支持更深一层(tmp_path):
    a = _adapter(tmp_path, [".venv/Scripts/python.exe", "scripts/engine.py"])
    # `.venv` 段跳过 → 落到 `scripts/`（它在 .venv 之外，是同源的那个）
    assert _plugin_subdir(a) == tmp_path / "scripts"


def test_subdir_全部绝对路径时回退_stem(tmp_path):
    """系统 python + 绝对脚本路径：推导不出插件目录，回退声明文件 stem。"""
    a = _adapter(tmp_path, ["C:/Python311/python.exe" if os.name == "nt" else "/usr/bin/python3"])
    assert _plugin_subdir(a) == tmp_path / "cmd_plugin"


def test_subdir_单段相对路径回退_stem(tmp_path):
    """`["python", "engine.py"]`（模板里的最简写法）—— 单段没有目录信息。"""
    a = _adapter(tmp_path, ["python", "engine.py"])
    assert _plugin_subdir(a) == tmp_path / "cmd_plugin"


def test_subdir_无声明文件时回退_name(tmp_path):
    p = _CmdPlugin(["pkg/.venv/Scripts/python.exe", "pkg/engine.py"])
    a = _PluginEngineAdapter(p, tmp_path, None)
    assert _plugin_subdir(a) == tmp_path / "pkg", "command 能推导时根本不看 stem"


def test_对照实验_官方模板的子目录与_command_一致():
    """拿**真实模板文件**跑一遍：三个进程外模板的 command 子目录都必须存在。

    这一条是「模板自己不会漂」的守卫 —— 上一族用假 adapter 测的是算法，这一条
    测的是仓库里那三个文件**真的写对了**。缺少 `requirements.txt` 的模板（tesseract
    的 venv 无第三方依赖，只有注释）也必须有那个目录。
    """
    import importlib.util
    ex = Path(__file__).resolve().parent.parent / "plugins" / "examples"
    for decl in ("pymupdf4llm_plugin.py", "tesseract_plugin.py", "faster_whisper_plugin.py"):
        spec = importlib.util.spec_from_file_location(f"_tpl_{decl[:-3]}", ex / decl)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)          # 只求类定义，不跑引擎
        cls = next(
            v for v in vars(mod).values()
            if isinstance(v, type) and issubclass(v, EnginePlugin) and v is not EnginePlugin
        )
        a = _PluginEngineAdapter(cls(), ex, ex / decl)
        sub = _plugin_subdir(a)
        assert sub.is_dir(), f"{decl} 的子目录不存在：{sub}"
        assert (sub / "engine.py").is_file(), f"{decl} 的子目录缺 engine.py：{sub}"
        assert (sub / "requirements.txt").is_file(), \
            f"{decl} 的子目录缺 requirements.txt（一键安装会返回 no-requirements）"


# ══════════════════════════════════════════════════════════════════════
# 进程内（trusted）执行边界 —— 第三方代码跑在主进程里
# ══════════════════════════════════════════════════════════════════════
#
# 进程外插件崩了，只是一个子进程退出；进程内插件崩了，异常会顺着调用链一路
# 冒到 adapter 层，而那里只留得下一句 `str(e)[:100]`（document.py 的兜底）——
# traceback 全丢，用户读到的是半句话。边界放在 adapter 才能两头都保住：
# 日志留住原始 traceback，用户拿到带插件名的原因。

class _BoomTrusted(EnginePlugin):
    name = "test-boom"
    category = "doc"
    trusted = True

    def process(self, file_path, config):
        raise ValueError("插件内部炸了")


class _BadReturnTrusted(EnginePlugin):
    name = "test-bad-return"
    category = "doc"
    trusted = True

    def process(self, file_path, config):
        return 123        # 契约要求 str 或 dict


def test_inproc_异常带出插件名与原始原因(tmp_path):
    adapter = _PluginEngineAdapter(_BoomTrusted(), tmp_path)
    with pytest.raises(FatalError) as ei:
        adapter.execute("x.pdf")
    msg = str(ei.value)
    assert "test-boom" in msg, "用户要知道是哪个插件"
    assert "ValueError" in msg, "类型名是排查的起点"
    assert "插件内部炸了" in msg, "原始原因不能丢"


def test_inproc_返回类型非法时显式报错(tmp_path):
    """插件返回 `None` 曾被包成 `{"text": None}` → 上层报「返回空结果」。

    那个说法把「插件坏了」讲成了「这份文件没内容」，而且零报错地放行到
    后续链路。这条钉住「错在插件」必须当场说清，而不是等到上层猜。
    """
    adapter = _PluginEngineAdapter(_BadReturnTrusted(), tmp_path)
    with pytest.raises(FatalError) as ei:
        adapter.execute("x.pdf")
    msg = str(ei.value)
    assert "test-bad-return" in msg
    assert "int" in msg, "要说出实际类型，否则插件作者不知道该返回什么"


def test_进程内插件不提供一键安装(tmp_path, monkeypatch):
    """`hasRequirements` 的语义是「能不能一键安装」，不是「目录里有没有那个文件」。

    ⚠️ 直接答「有没有 requirements.txt」会让进程内插件也亮起安装按钮，而依赖
    会被装进一个它**根本不加载**的 venv —— 界面说成功、插件仍不可用、零报错。
    """
    (tmp_path / "requirements.txt").write_text("somepkg==1.0\n", encoding="utf-8")
    adapter = _PluginEngineAdapter(_TrustedPlugin(), tmp_path)
    monkeypatch.setitem(PLUGIN_REGISTRY, "test-trusted",
                        {"adapter": adapter, "enabled": True, "loaded": True})

    it = {p["name"]: p for p in enumerate_plugins()}["test-trusted"]
    assert it["hasRequirements"] is False


def test_install_deps_拒绝进程内插件(tmp_path, monkeypatch):
    """入口严：前端门控之外，接口自己也要拒绝。

    没有这一条，curl 一下就能在插件目录里堆出一个永远不会被加载的 .venv，
    而界面上什么都不显示 —— 装的人以为成功了。
    """
    (tmp_path / "requirements.txt").write_text("somepkg==1.0\n", encoding="utf-8")
    adapter = _PluginEngineAdapter(_TrustedPlugin(), tmp_path)
    monkeypatch.setitem(PLUGIN_REGISTRY, "test-trusted",
                        {"adapter": adapter, "enabled": True, "loaded": True})

    res = install_plugin_deps("test-trusted")
    assert res["status"] == "inproc"
    assert not (tmp_path / ".venv").exists(), "拒绝之后不能留下任何 venv 目录"
