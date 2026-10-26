"""`core.deps.probe()` —— 「依赖装没装」的唯一判据。

运行：cd backend && pytest tests/test_deps.py

⚠️ 本文件守的是**判据本身在撒谎**这一类失效（不是「少了个 try」）：

  `importlib.util.find_spec(m) is not None` 看起来就是「依赖在不在」的标准写法，
  阅读时毫无异常感。但它回答的是「**能定位**这个模块吗」，而三种真实形态下
  「定位得到」≠「用得上」，且三种都是静默的：

  1. **namespace package** —— 目录里只有 `tokenizers.pyd`、没有 `__init__.py`。
     `find_spec` 返回非 None、`import` 也成功（得到一个空壳），里面却连
     `Tokenizer` 都没有。2026-10-27 实测于安装版 `_internal/tokenizers/`。
  2. **DLL 缺失** —— Python 层齐全，`__init__` 里加载原生库抛 `OSError`。
  3. **包的 `__init__` 自己抛** —— 缺可选依赖 / 版本冲突。

  前两条的后果是同一条：「检查说就绪 → 用户切过去 → 编码那一刻才炸」，而那时
  代价已经付出（选了引擎、点了开始）。所以本文件的重心不是 `probe` 返回什么，
  而是 **`find_spec` 与 `probe` 在同样的目录上给出相反结论**（两条对照实验）。
"""
import importlib.util
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.core.deps import probe, probe_venv  # noqa: E402


# ── 基本形状 ──

def test_可用时返回_None():
    """标准库永远可用 —— `None` = 可以跑。"""
    assert probe("json", attrs=("dumps",)) is None


def test_模块不存在时返回原因():
    reason = probe("_limo_no_such_module_zzz")
    assert reason and "_limo_no_such_module_zzz" in reason


def test_缺属性时返回原因():
    """`json` 里没有 `Tokenizer` —— 只查模块存在性的判据会漏掉这一层。"""
    reason = probe("json", attrs=("Tokenizer",))
    assert reason and "缺少 Tokenizer" in reason


def test_不传_attrs_时只判存在性():
    """向后兼容的弱判据：`attrs` 缺省 = 只问能不能导入（文档里说要用例都传）。"""
    assert probe("json") is None


# ── 对照实验：`find_spec` 与 `probe` 在同一个目录上给出相反结论 ──

def _make_namespace_pkg(tmp: str, name: str) -> None:
    """造一个 namespace package：**目录里没有 `__init__.py`**。

    这正是安装版 `_internal/tokenizers/` 的形态（7.5MB `tokenizers.pyd`、无
    `__init__.py`）—— Python 把这种目录当 namespace package，`import` 成功但
    里面一个属性都没有。
    """
    pkg = os.path.join(tmp, name)
    os.makedirs(pkg, exist_ok=True)
    with open(os.path.join(pkg, "binary_ext.pyd"), "wb") as f:
        f.write(b"\x00")           # 与真形态一致：只有二进制、没有 Python 层


def test_对照实验_namespace_package_find_spec_说找得到(tmp_path, monkeypatch):
    """⚠️ 本文件的核心断言：旧判据在这里说「装好了」，而它实际跑不了。

    ⚠️ 必须有这一半 —— 只断 `probe` 报错是不够的：`probe` 若哪天退化成
    `find_spec` 的包装，单看它自己的断言仍可能全绿（两边一起错时没有任何可见征兆）。
    """
    name = "_limo_ns_probe_pkg"
    _make_namespace_pkg(str(tmp_path), name)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, name, raising=False)

    assert importlib.util.find_spec(name) is not None, \
        "对照组前提不成立：find_spec 本来应该「找得到」"

    reason = probe(name, attrs=("Tokenizer",))
    assert reason and "已导入但缺少 Tokenizer" in reason, \
        "probe 必须识破这个空壳 —— 这正是 find_spec 看不出来的那件事"
    assert "安装不完整" in reason, "要说清是「装残了」而不是「没装」"


def test_对照实验_导入抛_OSError_也算不可用(tmp_path, monkeypatch):
    """DLL 缺失形态：`__init__.py` 里加载原生库失败抛 `OSError`。

    ⚠️ 用 `except ImportError` 的写法**抓不到它**（那是 `OSError` 的子类兄弟，
    不是子类）—— 这正是 `Embedder.__init__` 原先漏掉的那一类。
    """
    name = "_limo_broken_probe_pkg"
    pkg = tmp_path / name
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        "raise OSError('DLL load failed while importing native: 找不到指定的模块。')\n",
        encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, name, raising=False)

    assert importlib.util.find_spec(name) is not None, "对照组前提不成立"

    reason = probe(name)
    assert reason and "OSError" in reason, "异常类型要带出来（它决定用户怎么修）"
    assert "DLL load failed" in reason, "原始信息要保留 —— 那是唯一的线索"


def test_自检自己炸了不会抛出去():
    """`probe` 的调用方是「回答这台机器能不能跑」，它抛出只会把缺依赖变成设置页 500。"""
    # 目录名非法（含空字节）会让 importlib 自己抛 —— 属于「任何异常」这一类。
    assert probe("\x00bad") is not None


# ── probe_venv：进程外插件的 venv 就位判据 ──
#
# ⚠️ 这一族守的是「装好了却说没装」与「没装却说装好了」两个方向。前者更常见：
# 在 precheck 里用 `probe()` 问主进程能不能 import 子 venv 的依赖，答案是永远不能。

def _make_venv(root, *, python=True, cfg_home=True, base_python=True,
               site_packages=True, modules=()):
    """造一个**结构完整**的假 venv（不执行任何 Python，纯文件系统）。"""
    venv = root / "venv"
    venv.mkdir(parents=True)

    base_dir = root / "base"
    if base_python:
        base_dir.mkdir(exist_ok=True)
        (base_dir / ("python.exe" if os.name == "nt" else "python3")).write_bytes(b"")
    else:
        base_dir.mkdir(exist_ok=True)      # 目录在、解释器不在 —— 用户清理过 base python

    if python:
        py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python3")
        py.parent.mkdir(parents=True, exist_ok=True)
        py.write_bytes(b"")

    if cfg_home:
        (venv / "pyvenv.cfg").write_text(f"home = {base_dir}\nversion = 3.11.9\n",
                                         encoding="utf-8")

    if site_packages:
        sp = (venv / "Lib/site-packages") if os.name == "nt" else (venv / "lib/python3.11/site-packages")
        sp.mkdir(parents=True, exist_ok=True)
        for m in modules:
            (sp / m).mkdir()               # 包目录形态（最常见）

    return venv


def test_probe_venv_就绪返回_None(tmp_path):
    venv = _make_venv(tmp_path, modules=("faster_whisper", "ctranslate2"))
    assert probe_venv(venv, modules=("faster_whisper", "ctranslate2")) is None


def test_probe_venv_没建时说要建(tmp_path):
    reason = probe_venv(tmp_path / "nope", hint="点「一键安装依赖」")
    assert reason and "未创建独立环境" in reason
    assert "一键安装依赖" in reason, "hint 必须拼进去 —— 那是用户唯一能照着做的动作"


def test_probe_venv_缺解释器(tmp_path):
    venv = _make_venv(tmp_path, python=False)
    reason = probe_venv(venv)
    assert reason and "找不到 Python 解释器" in reason


def test_probe_venv_缺_pyvenv_cfg(tmp_path):
    """没有 pyvenv.cfg 的目录**不是** venv，哪怕里面有 python.exe。"""
    venv = _make_venv(tmp_path, cfg_home=False)
    reason = probe_venv(venv)
    assert reason and "pyvenv.cfg" in reason


def test_probe_venv_base_python_已失效(tmp_path):
    """venv 目录完好、python.exe 就在那儿，但它依赖的那台 Python 被删了。

    这是 uv 托管 Python 的典型失效：**只看 `python.exe` 在不在的判据会说「装好了」**，
    一执行就报 `No Python at '...'`。
    """
    venv = _make_venv(tmp_path, base_python=False)
    reason = probe_venv(venv)
    assert reason and "已被移动或删除" in reason, "要说清是 base python 没了，不是依赖没装"


def test_probe_venv_缺_site_packages(tmp_path):
    venv = _make_venv(tmp_path, site_packages=False)
    reason = probe_venv(venv)
    assert reason and "site-packages" in reason


def test_probe_venv_缺依赖(tmp_path):
    venv = _make_venv(tmp_path, modules=("faster_whisper",))
    reason = probe_venv(venv, modules=("faster_whisper", "ctranslate2"),
                        hint="点「一键安装依赖」")
    assert reason and "ctranslate2" in reason and "faster_whisper" not in reason, \
        "只报**缺的那个**，别把已装的也列出来"
    assert "一键安装依赖" in reason


def test_probe_venv_模块以单文件形式存在也算装上(tmp_path):
    """有的包装成扁平 `.py`（少数纯 Python 小包），不能只认目录。"""
    venv = _make_venv(tmp_path)
    sp = (venv / "Lib/site-packages") if os.name == "nt" else (venv / "lib/python3.11/site-packages")
    (sp / "flatmod.py").write_text("", encoding="utf-8")
    assert probe_venv(venv, modules=("flatmod",)) is None


def test_对照实验_同一个_venv_probe_说没装而_probe_venv_说装好了(tmp_path, monkeypatch):
    """⚠️ 本族的核心断言：**为什么进程外插件不能用 `probe`**。

    子 venv 的 site-packages 不在主进程 `sys.path` 里 —— 用 `probe` 检查会
    永远报「无法导入」，于是每个**装好了**的插件都显示不可用（与「检查不出来」
    相反的失效：用户照着提示反复重装，而东西一直是好的）。
    """
    venv = _make_venv(tmp_path, modules=("_limo_only_in_subvenv",))

    assert probe_venv(venv, modules=("_limo_only_in_subvenv",)) is None, \
        "对照组前提：这个包确实躺在子 venv 的 site-packages 里"

    reason = probe("_limo_only_in_subvenv")
    assert reason and "无法导入" in reason, \
        "同一份依赖，主进程视角就是「没有」—— 这正是裸用 probe 的失效形态"


def test_probe_venv_自检自己炸了不会抛出去(tmp_path, monkeypatch):
    """WinError 448（不受信任装入点）这类 `OSError` 同样只该是「不可用」。"""
    import app.core.deps as deps

    def boom(_):
        raise OSError("WinError 448: 该装入点不受信任")

    monkeypatch.setattr(deps, "venv_python", boom)
    reason = probe_venv(tmp_path)
    assert reason and "WinError 448" in reason


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
