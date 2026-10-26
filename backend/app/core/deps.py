"""第三方依赖的可用性探测 —— 真 import 一次，而不是只问「找不找得到这个模块」。

## 为什么不能只用 `importlib.util.find_spec`

它回答的是「**能定位**这个模块吗」，不是「**能用**它吗」。两者在三种真实场景下
分叉，而三种都是静默的：

1. **namespace package**（2026-10-27 实测于安装版）：目录里只有 `tokenizers.pyd`、
   没有 `__init__.py`。`find_spec("tokenizers")` 返回非 None、`import tokenizers`
   也成功（得到一个空的 namespace 模块），但里面连 `Tokenizer` 都没有 ——
   于是「依赖检查」说装好了，直到真正编码那一刻才炸。而那时用户已经选好了引擎、
   点了开始，代价是一次失败任务。
2. **DLL 缺失**（onnxruntime / numpy 的常见形态）：Python 层齐全，原生库加载失败。
   `find_spec` 完全看不出来。
3. **包的 `__init__` 自己 import 失败**（缺可选依赖、版本冲突）：同上。

## 代价

真 import 会执行包的顶层代码（onnxruntime 首次约几百毫秒 + 常驻内存）。故本模块
只给「要回答就绪与否」或「用户即将使用它」的路径用，**不要放进热循环** ——
第二次及以后由 `sys.modules` 天然命中（微秒级），不需要另做缓存。

## 形状

`probe()` 返回 `None` 表示可用、`str` 表示给用户看的原因 —— 与各引擎的
`precheck()`（`app.engines.plugin_loader.EnginePlugin.precheck` /
`LocalEmbeddingEngine.precheck`）**同一个形状**，消费方不需要转译。
"""

import importlib
import os
from pathlib import Path
from typing import Optional


def probe(module: str, *, attrs: tuple[str, ...] = ()) -> Optional[str]:
    """`module` 是否**真的可用**。`None` = 可用；否则返回一句可直接展示的原因。

    `attrs` 是该模块**真正会被调用方用到**的属性名。只问模块在不在会被
    namespace package 骗过（见模块文档第 1 条），所以要用例都应当传上它。

    ⚠️ 任何异常都折成「不可用」而不是让自检自己炸：这个函数的调用方是
    「回答这台机器能不能跑」，它抛出只会把「缺依赖」变成「设置页报错」。
    """
    try:
        mod = importlib.import_module(module)
    except Exception as exc:  # noqa: BLE001 —— ImportError / OSError(DLL) / __init__ 自己抛的
        return f"{module} 无法导入（{type(exc).__name__}: {exc}）"

    missing = [a for a in attrs if not hasattr(mod, a)]
    if missing:
        return (f"{module} 已导入但缺少 {'/'.join(missing)}"
                f" —— 安装不完整（常见于只有二进制、缺 Python 层的包）")
    return None


def venv_python(venv_dir: Path | str) -> Optional[Path]:
    """venv 的解释器路径（**已确认存在**）；没有则 `None`。"""
    venv = Path(venv_dir)
    for rel in ("Scripts/python.exe", "bin/python3", "bin/python"):
        p = venv / rel
        if p.is_file():
            return p
    return None


def _venv_site_packages(venv_dir: Path) -> list[Path]:
    """venv 的 site-packages 目录（POSIX 下带 `python3.X` 中间层）。"""
    win = venv_dir / "Lib" / "site-packages"
    if win.is_dir():
        return [win]
    return sorted(p for p in (venv_dir / "lib").glob("python*/site-packages") if p.is_dir())


def _venv_base_python(venv_dir: Path) -> Optional[Path]:
    """`pyvenv.cfg` 里那台 base python —— 它失效时 venv 启动报 "No Python at ..."。

    venv 的 `python.exe` 只是个转发器：真正跑的是 base python。用户升级/清理过
    那台 Python（uv 托管的那种尤其常见）之后，venv 目录看起来完好、`python.exe`
    就在那儿，一启动就失败。只看 `python.exe` 在不在的判据会说「装好了」。
    """
    cfg = venv_dir / "pyvenv.cfg"
    if not cfg.is_file():
        return None
    try:
        home = None
        for line in cfg.read_text(encoding="utf-8").splitlines():
            # 按 key 判而不是 `startswith("home =")`：`=` 两头的空格在各家工具下
            # 不一致（`python -m venv` 与 uv 都写空格，手改的常写成 `home=X`）
            key, sep, val = line.partition("=")
            if sep and key.strip().lower() == "home":
                home = val.strip()
                break
        if not home:
            return None
        return Path(home) / ("python.exe" if os.name == "nt" else "python3")
    except OSError:
        return None


def probe_venv(venv_dir: Path | str, modules: tuple[str, ...] = (), *, hint: str = "") -> Optional[str]:
    """**进程外插件**的独立 venv 是否就位。`None` = 就位；否则返回给用户看的原因。

    ## 为什么不能用 `probe()`

    进程外插件的依赖装在**子 venv** 里，主进程 `sys.path` 中根本没有它们 ——
    在 `precheck` 里调 `probe("pymupdf4llm")` 会**永远返回「无法导入」**，
    于是每个**装好了**的插件都显示不可用。那是与「检查不出来」相反的失效：
    用户照着提示反复重装，而东西一直是好的。

    ## 为什么只能做文件系统检查

    `precheck` 会被 `GET /api/plugins` 调用，契约明写「必须轻：不要起子进程」。
    真正 import 子 venv 的代价是一次 Python 冷启动（数百毫秒到数秒），
    而这个接口在用户打开设置页时就要返回。

    ⚠️ 这是**近似**：目录在 ≠ 一定能 import（DLL 缺失时目录也在）。真正的失败
    仍由首次执行显式报错兜底 —— 与「不静默降级」一致，区别只是把最可能的几种
    缺失（没建 venv / 没装依赖 / base python 已失效）从「点了生成才知道」提前到
    设置页。

    `modules` 传的是 **import 名**（`import xxx` 里的那个），不是 PyPI 包名
    （`python-docx` → `docx`）。

    `hint` 会拼进原因句尾，通常写补齐办法（点「一键安装」/ 手动 pip 命令）。

    ⚠️ **本条是「这个 venv 能不能用」的唯一判据**：`plugin_loader._venv_valid`
    （`depsInstalled` 字段）委托到这里。那份实现曾经自己读一遍 `pyvenv.cfg`，
    两份判据的失效形态是「设置页说依赖已装、自检说没装」—— 同一个 venv，两个答案。
    """
    venv = Path(venv_dir)
    try:
        if not venv.is_dir():
            return f"未创建独立环境（{venv.name}）" + (f" —— {hint}" if hint else "")

        if venv_python(venv) is None:
            return (f"独立环境不完整：{venv.name} 下找不到 Python 解释器 —— "
                    f"删掉该目录后重新「一键安装」") + (f"（{hint}）" if hint else "")

        base = _venv_base_python(venv)
        if base is None:
            return (f"独立环境不完整：{venv.name} 缺 pyvenv.cfg 或其中的 home —— "
                    f"删掉该目录后重新「一键安装」") + (f"（{hint}）" if hint else "")
        if not base.exists():
            return (f"独立环境已失效：它依赖的 Python（{base}）已被移动或删除 —— "
                    f"删掉 {venv.name} 后重新「一键安装」") + (f"（{hint}）" if hint else "")

        sites = _venv_site_packages(venv)
        if not sites:
            return (f"独立环境不完整：找不到 {venv.name} 的 site-packages —— "
                    f"删掉该目录后重新「一键安装」") + (f"（{hint}）" if hint else "")

        missing = [
            m for m in modules
            if not any((s / m).is_dir() or (s / f"{m}.py").is_file() for s in sites)
        ]
        if missing:
            return (f"缺少依赖 {'/'.join(missing)}（{venv.name} 里没装）"
                    + (f" —— {hint}" if hint else ""))
        return None
    except OSError as e:
        # WinError 448（不受信任装入点，常见于 AppData 下 uv 托管的 python）——
        # 路径遍历本身就可能抛，这同样是「用不了」，不能让它冒到 /api/plugins 变成 500
        return f"独立环境不可访问（{type(e).__name__}: {e}）" + (f" —— {hint}" if hint else "")
