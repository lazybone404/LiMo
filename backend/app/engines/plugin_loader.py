"""
引擎插件加载器 — 从 {data_dir}/plugins/*.py 自动发现并注册第三方引擎。

EnginePlugin v1 契约（2026-08-02 正式化，以用户定稿 v2 为准）：

- 默认进程外：`command` 指向独立 venv，subprocess 调用（依赖隔离 / 崩溃隔离 / 长任务）
- trusted 例外：插件声明 `trusted=true` → 允许进程内 import（纯技术声明：可进程内 import，
  崩溃/依赖风险插件作者自担；**不涉及任何协议判断**）
- 门控全类别统一：command 有值→external；trusted→inproc；无 command 且未 trusted→拒绝加载
- 进程外契约：stdin JSON（file/cancel_file/config/...config）+ stdout JSON（text/segments/pages...）
- v0 兼容：stdin 仍带 `--input <path>` argv；stdout 非 JSON 回退纯文本（旧插件零改动可用）
- 主程序不处理任何插件协议维度（不判断/不警告/不声明），license 字段仅前端展示
- PLUGIN_REGISTRY 登记所有已发现插件（含门控拒绝/禁用的），供管理 API 枚举
"""

import ast
import importlib.util
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

from app.core.registry import EngineRegistry
from app.core.protocols import Engine
from app.core.deps import probe_venv

logger = logging.getLogger(__name__)

PLUGIN_API_VERSION = 1


class EnginePlugin:
    """插件基类 — 第三方引擎继承并声明元数据。

    例 (~/.limo/plugins/my_plugin.py，进程外模式，推荐)：
        from app.engines.plugin_loader import EnginePlugin
        class MyEngine(EnginePlugin):
            name = "my-engine"
            category = "doc"
            command = ["my_engine/.venv/Scripts/python.exe", "my_engine/engine.py"]

    例 (trusted 进程内模式，仅轻量无重依赖插件)：
        class MyLightEngine(EnginePlugin):
            name = "my-light"
            trusted = True   # 显式信任声明：允许进程内 import，风险自担
            def process(self, file_path, config):
                return "markdown"
    """

    # ── 运行模式（二选一）──
    command: Optional[list[str]] = None   # 有值=进程外（默认主体）；None 需 trusted=true
    trusted: bool = False                 # 显式信任声明：允许进程内 import（纯技术含义）

    # ── 元数据 ──
    category: str = "doc"                 # 开放字符串（v1 生效：doc/ocr/asr）
    name: str = ""
    license: str = "Unknown"              # SPDX（仅前端展示，无警告语义）
    description: str = ""
    file_types: list[str] = []            # 如 [".pdf"] → capabilities fileTypeMap 自动归入
    display_name: str = ""                # 用户向展示名，缺省回退 name
    user_desc: str = ""                   # 人话能力描述
    config_schema: list[dict] = []        # v1 简版配置声明（前端渲染简单表单）
    namespace: str = "engine"             # 注册表命名空间（v1 恒 engine，为 adapter/tool 预留）
    permissions: list[str] = []           # 预留空位（权限模型后置）
    api_version: int = PLUGIN_API_VERSION

    timeout: int = 300                    # 子进程超时秒数（仅进程外模式）

    # ── Engine 协议适配 ──

    def accept(self, file_path: str) -> bool:
        if not self.file_types:
            return True
        return any(file_path.lower().endswith(ext) for ext in self.file_types)

    def process(self, file_path: str, config: dict):
        """进程内模式实现（trusted 例外）。返回 str（text）或 dict（segments 等）。"""
        raise NotImplementedError

    def precheck(self) -> Optional[str]:
        """就绪自检（可选）。返回**给用户看的**不可用原因，`None` = 可以跑。

        默认实现返回 `None`（没有额外前置条件）—— 插件作者不重写它，语义就是
        「装上了就能用」。需要外部依赖 / 模型文件 / 密钥的插件重写它。

        ⚠️ **必须「报告」而不是「第一次调用时抛异常」**：抛异常的那条路上，用户
        已经选好了引擎、点了生成，代价是一次失败任务；而自检发生在设置页/启动时，
        用户还没付出任何东西。这也是 `core/deps.probe()` 那个形状的来源 ——
        两者都返回 `None | str`，消费方不需要转译。

        ⚠️ **这一条进契约、而 `precheck`/`warmup`/`release` 三个同类钩子不进
        `EmbeddingEngine` 协议，不是双标**：那个协议是 `runtime_checkable`，
        加成员会让**云端引擎不再满足它**（云端确实没有「依赖装没装」这回事）；
        而 `EnginePlugin` 是普通基类，加一个有默认实现的成员不会排斥任何子类。

        ⚠️ 会被 `GET /api/plugins`（设置页插件页）调用，故**必须轻**：不要在这里
        加载模型 / 起子进程。`core.deps.probe()` 的「真 import 一次」是可接受的上界，
        它的第二次及以后由 `sys.modules` 命中。
        """
        return None


# ── 加载门控 ──

def _resolve_exec_mode(plugin: EnginePlugin) -> Optional[str]:
    """加载门控（全类别统一）：command→external；trusted→inproc；无 command 且未 trusted→拒绝"""
    if plugin.command:
        return "external"
    if plugin.trusted:
        return "inproc"
    logger.warning(
        "插件 %s 无 command 且未声明 trusted=true，拒绝加载（默认进程外）", plugin.name
    )
    return None


# ── 契约解析与结果分派（纯函数，可单测）──

def _parse_stdout(raw: str) -> dict:
    """进程外 stdout 解析：JSON 优先；非 JSON 回退 v0 纯文本（doc/ocr→text、asr→单段）"""
    s = raw.strip()
    if s.startswith("{"):
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            pass
    return {"text": s, "_v0": True}


def _dispatch_result(category: str, payload: dict, engine_name: str):
    """按 category 分派为结构化结果（类别开放，未知类别默认 doc 契约）。

    修复 v0 返回纯 str 导致消费方 .text/.segments 访问崩的核心 bug。
    """
    if category == "asr":
        from app.engines.transcript import TranscriptResult, TranscriptSegment
        segs = [
            TranscriptSegment(
                float(s.get("start", 0)), float(s.get("end", 0)), s.get("text", "")
            )
            for s in payload.get("segments", [])
        ]
        if not segs and payload.get("text"):   # v0 纯文本 asr → 单段兜底
            segs = [TranscriptSegment(0, 0, payload["text"])]
        full = payload.get("full_text") or " ".join(x.text for x in segs)
        return TranscriptResult(
            language=payload.get("language", "zh"),
            duration=payload.get("duration", segs[-1].end if segs else 0),
            segments=segs,
            full_text=full,
        )
    if category == "ocr":
        from app.engines.ocr import OCRResult
        return OCRResult(
            text=payload.get("text", ""),
            engine=engine_name,
            confidence=float(payload.get("confidence", 0.0)),
        )
    from app.engines.doc_types import DocResult
    return DocResult(
        text=payload.get("text", ""),
        pages=int(payload.get("pages", 1)),
        engine=engine_name,
        is_scanned=bool(payload.get("is_scanned", False)),
        scanned_pages=int(payload.get("scanned_pages", 0)),
    )


# ── Plugin adapter: wraps EnginePlugin into Engine protocol ──

class _PluginEngineAdapter:
    """将 EnginePlugin 适配为 EngineRegistry 兼容的 Engine 协议。

    execute 按 category 分派返回 DocResult/OCRResult/TranscriptResult，
    消费方（document/video/image）已按结构化字段消费，零改动。
    """

    def __init__(self, plugin: EnginePlugin, plugin_dir: Path, source_file: Path | None = None):
        self._plugin = plugin
        self._plugin_dir = plugin_dir
        # 声明文件（plugins/xxx.py）——子目录命名规范「声明文件同名的子目录」，
        # 依赖它解析子目录（name 可能与声明文件 stem 不同，如 pymupdf4llm_heavy.py 里 name=pymupdf4llm）
        self._source_file = source_file
        self.name = plugin.name
        self.license = plugin.license
        self.description = plugin.description
        # 从插件复制必要的 Engine 属性
        self.supported_modes = getattr(plugin, 'supported_modes', [])
        self.capabilities = getattr(plugin, 'capabilities', [])
        self.file_types = plugin.file_types  # capabilities fileTypeMap 动态归入依赖此属性
        # v1 元数据（capabilities 暴露）
        self.is_plugin = True
        self.display_name = plugin.display_name or plugin.name
        self.user_desc = plugin.user_desc or ""
        self.config_schema = plugin.config_schema or []
        self.namespace = plugin.namespace or "engine"
        self.api_version = plugin.api_version or PLUGIN_API_VERSION
        self.trusted = plugin.trusted
        # 插件自身配置（Phase 4 由 cfg["plugin_configs"] 填充；空 dict 时插件用默认值）
        self.plugin_config: dict = {}
        self._exec_mode = _resolve_exec_mode(plugin)

    def accept(self, file_path: str) -> bool:
        return self._plugin.accept(file_path)

    def run(self, file_path: str, **kwargs):
        """Engine 协议入口"""
        return self.execute(file_path, **kwargs)

    def execute(self, file_path: str, **kwargs):
        cancel_event = kwargs.pop("cancel_event", None)
        if self._exec_mode == "external":
            raw = self._run_external(file_path, kwargs, cancel_event)
            payload = _parse_stdout(raw)
        else:  # inproc（trusted 例外）
            out = self._plugin.process(file_path, kwargs)
            # dict 透传（trusted asr 引擎返回 segments dict 场景）；str 包 {"text"}
            payload = out if isinstance(out, dict) else {"text": out}
        return _dispatch_result(self._plugin.category, payload, self.name)

    def precheck(self) -> Optional[str]:
        """转发插件的自检。异常兜成「不可用」—— 自检自己炸了，结论就是不可用。

        ⚠️ **兜底必须在这里、而不是指望每个消费方**：`GET /api/plugins` 与
        `vector_runtime` 都会调它，而插件是任意第三方 Python —— 其中一处的
        try/except 漏了，症状就是「打开设置页 500」，看起来像主程序坏了。
        """
        try:
            return self._plugin.precheck()
        except Exception as e:  # noqa: BLE001
            return f"插件自检失败：{type(e).__name__}: {e}"

    def _run_external(self, file_path: str, config: dict, cancel_event=None) -> str:
        """进程外执行：subprocess Popen 调用独立 venv，stdin 传 JSON config。

        stdin JSON: {file, cancel_file, config(插件自身配置), ...kwargs}
        argv 保留 `--input <path>`（v0 插件兼容）。
        cancel: 监视线程写标记文件 → 宽限 3s → kill（timeout 永远兜底）。
        """
        safe = {
            k: v for k, v in config.items()
            if isinstance(v, (str, int, float, bool, list, dict, type(None)))
        }
        safe["file"] = file_path
        # 插件自身配置进契约（plugin_configs → set_plugin_config 更新；空 dict 插件用默认值）
        safe["config"] = self.plugin_config
        cancel_file = ""
        if cancel_event is not None:
            # cancel_file 用绝对路径（子进程 cwd 与主程序不同）
            cancel_file = os.path.join(
                tempfile.gettempdir(),
                f"limo_cancel_{os.getpid()}_{threading.get_ident()}.flag",
            )
            safe["cancel_file"] = cancel_file

        # command 相对路径 → 基于插件目录解析为绝对路径
        # Windows CreateProcess 对带子目录的相对路径参数解析不可靠
        cmd = [
            str(self._plugin_dir / part) if not Path(part).is_absolute() else part
            for part in self._plugin.command
        ]
        proc = subprocess.Popen(
            cmd + ["--input", file_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            cwd=str(self._plugin_dir),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

        # 取消监视线程：cancel_event 置位 → 写标记 → 宽限 3s → 强杀
        stop = threading.Event()

        def _monitor():
            while not stop.is_set():
                if cancel_event and cancel_event.is_set():
                    try:
                        Path(cancel_file).write_text("cancel", encoding="utf-8")
                    except OSError:
                        pass
                    time.sleep(3)
                    if proc.poll() is None:
                        proc.kill()
                    return
                time.sleep(0.2)

        mon = threading.Thread(target=_monitor, daemon=True)
        mon.start()
        try:
            out, err = proc.communicate(json.dumps(safe), timeout=self._plugin.timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            raise RuntimeError(
                f"Plugin {self.name} timed out after {self._plugin.timeout}s"
            ) from None
        finally:
            stop.set()
            if cancel_file:
                try:
                    os.unlink(cancel_file)
                except OSError:
                    pass
        if proc.returncode != 0:
            raise RuntimeError(
                err.strip() or f"Plugin exited with code {proc.returncode}"
            )
        return out


# ── 插件注册表（Phase 4 管理 API 的数据基础）──
# name → {adapter, meta, path, source, enabled, loaded}
#   enabled — config 期望状态（运行时 enable/disable 同步；前端开关位置）
#   loaded  — 是否已注册进 EngineRegistry（仅启动时决定；enabled != loaded → 待重启生效）
PLUGIN_REGISTRY: dict[str, dict] = {}


# ── 加载入口 ──

def _base_name(node: ast.expr) -> str:
    """基类表达式的名字：`EnginePlugin` / `sdk.EnginePlugin` 都返回 `EnginePlugin`。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _peek_plugin_meta(py_file: Path) -> Optional[dict]:
    """**不执行插件代码**，从源码静态读出声明类上的字符串字面量属性（至少含 `name`）。

    只服务于一条路径：`plugin_states[name] is False` 时**跳过 `exec_module`**（P1-02）。
    插件名恰恰要执行完才知道，而「禁用」的全部意义就是别执行它的代码 —— 只能从源码读。

    ⚠️ 读不出来就返回 None，调用方退回「先执行、再判」的旧行为并记 debug：静态解析
    撑不住所有写法（动态拼 name、多级继承、`__slots__` 之类），**降级方向必须安全**
    （宁可多执行一个文件，也不能因为解析不了就漏装一个插件）。这条降级是**可见的**
    （有日志），不是静默的。
    """
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if not any(_base_name(b) == "EnginePlugin" for b in node.bases):
            continue
        meta: dict = {}
        for stmt in node.body:
            if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                    and isinstance(stmt.targets[0], ast.Name)
                    and isinstance(stmt.value, ast.Constant)
                    and isinstance(stmt.value.value, str)):
                meta[stmt.targets[0].id] = stmt.value.value
        if "name" in meta:
            return meta
    return None


def init_plugins(config: dict):
    """扫描 {data_dir}/plugins/ 目录，加载所有插件。

    在 init_engines() 之后调用，确保内置引擎已注册。
    插件覆盖规则：同 category + name 插件覆盖内置引擎（用户优先）。

    门控：command→进程外（默认）；trusted→进程内；无 command 且未 trusted→拒绝（warning）。
    启用：plugin_states[name]==False → **连代码都不加载**（静态度出名字后整个跳过），
          仍保留 PLUGIN_REGISTRY 条目供前端展示与「重新启用」；
          未配置默认 enabled（向后兼容，现有插件不因新配置而禁用）。

    ⚠️ 「禁用」的语义是**不执行它的顶层代码**（P1-02）：插件是任意 Python，
    `exec_module` 那一刻它就能联网/写盘，与主程序同权限（`docs/plugins.md` 的告示）。
    此前的实现是先 `exec_module` 再读 enabled —— 于是「停用」只免注册、代码照跑，
    与前端「重启后停用」的文案不符。
    """
    plugins_dir = Path(config.get("data_dir", "")) / "plugins"
    if not plugins_dir.exists():
        return  # 目录不存在优雅跳过（打包版用户可能从未装插件）

    states = config.get("plugin_states", {}) or {}
    plugin_configs = config.get("plugin_configs", {}) or {}

    loaded = 0
    # 扫描范围：plugins/ 顶层 *.py + 一层子目录 *.py
    # 子目录支持 git clone 仓库布局（plugins/<repo>/<plugin>.py），
    # 排除 __pycache__ 等点开头目录 + 常见非插件基础设施文件
    _SKIP = {"setup.py", "conftest.py", "__init__.py"}
    py_files = [
        p for p in sorted(plugins_dir.glob("*.py")) + sorted(plugins_dir.glob("*/[!.]*.py"))
        if p.name not in _SKIP and not (p.name.startswith("test_") or p.name.endswith("_test.py"))
    ]
    for py_file in py_files:
        try:
            # ── 禁用即不加载（P1-02）──
            # 插件名要执行完才知道，而「禁用」的意义就是别执行它的代码 → 先从源码
            # 静态读名字，命中禁用名单就整个跳过（`exec_module` 一次都不发生）。
            # 读不出名字（None）则退回旧行为：执行后再判 enabled —— 见 `_peek_plugin_meta`。
            _peeked = _peek_plugin_meta(py_file)
            if _peeked is not None and states.get(_peeked["name"], True) is False:
                PLUGIN_REGISTRY[_peeked["name"]] = {
                    "adapter": None,        # 没有实例：这条路径的全部意义就是不实例化它
                    "meta": None,
                    "path": str(py_file),
                    "source": "user",
                    "category": _peeked.get("category", ""),
                    "enabled": False,
                    "loaded": False,
                    "reason": "disabled-by-config",
                }
                logger.info("Plugin disabled by config，代码未加载: %s (%s)",
                            _peeked["name"], py_file.name)
                continue
            if _peeked is None:
                logger.debug("插件元信息无法静态解析，改为执行后再判 enabled: %s", py_file.name)

            # 模块名带 parent（顶层="plugins"，子目录=仓库名）——同名 stem 不冲突
            spec = importlib.util.spec_from_file_location(
                f"_limo_plugin_{py_file.parent.name}_{py_file.stem}", py_file
            )
            if spec is None or spec.loader is None:
                logger.warning("Cannot load plugin %s: invalid spec", py_file.name)
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            # 查找 EnginePlugin 子类
            found = False
            for obj in vars(mod).values():
                if (
                    isinstance(obj, type)
                    and issubclass(obj, EnginePlugin)
                    and obj is not EnginePlugin
                ):
                    found = True
                    try:
                        instance = obj()
                    except Exception as e:
                        logger.warning(
                            "Plugin %s instantiation failed: %s", py_file.name, e
                        )
                        continue

                    adapter = _PluginEngineAdapter(instance, py_file.parent, py_file)
                    # 回填落盘配置（P1-02）：此前只有 `PUT /plugins/{name}/config` 会写
                    # `adapter.plugin_config`（内存），启动路径从不读 `plugin_configs`
                    # → 重启后插件拿到的恒是空 dict，用户保存的配置静默丢失（插件退回
                    # 默认值，而 UI 上那条 config 明明还在）。键缺失 = 用户没配过 = 空 dict。
                    adapter.plugin_config = dict(plugin_configs.get(instance.name) or {})

                    # 门控：无 command 且未 trusted → 拒绝（登记但不注册）
                    if adapter._exec_mode is None:
                        PLUGIN_REGISTRY[instance.name] = {
                            "adapter": adapter,
                            "meta": instance,
                            "path": str(py_file),
                            "source": "user",
                            "enabled": False,
                            "loaded": False,
                            "reason": "no-command-no-trusted",
                        }
                        logger.warning(
                            "Plugin %s rejected: 无 command 且未声明 trusted=true",
                            instance.name,
                        )
                        continue

                    enabled = states.get(instance.name, True)
                    PLUGIN_REGISTRY[instance.name] = {
                        "adapter": adapter,
                        "meta": instance,
                        "path": str(py_file),
                        "source": "user",
                        "enabled": enabled,
                        # loaded = 是否已注册进 EngineRegistry（仅在启动时决定；运行时 enable/disable 不改变它）
                        "loaded": enabled,
                    }
                    if enabled:
                        EngineRegistry.register(instance.category, instance.name, adapter)
                        logger.info(
                            "Plugin loaded: %s/%s (%s) [%s]",
                            instance.category,
                            instance.name,
                            adapter._exec_mode,
                            py_file.name,
                        )
                        loaded += 1
                    else:
                        logger.info(
                            "Plugin disabled by config: %s/%s", instance.category, instance.name
                        )

            if not found:
                logger.debug("No EnginePlugin subclass found in %s", py_file.name)

        except Exception as e:
            logger.warning("Plugin %s failed to load: %s", py_file.name, e)

    if loaded > 0:
        logger.info("Plugin loader: %d plugin(s) loaded from %s", loaded, plugins_dir)


# ── 管理 API 辅助（Phase 4 使用）──

# ── 一键安装依赖（进程外插件）──

# 国内镜像源（pip 默认直连 PyPI 慢/易失败；项目全局偏好）
PIP_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"


def _plugin_subdir(adapter) -> Path:
    """插件执行子目录（含 engine.py / requirements.txt / .venv）。

    **优先从 `command` 推导**：那里的相对路径就是 `_run_external` 执行时真正
    解析的那个（`self._plugin_dir / part`），两处必须同源。推导不出才回退到
    声明文件 stem。

    ⚠️ 此前只看 stem，于是 `pymupdf4llm_plugin.py` + `pymupdf4llm/` 这种最常见
    的写法在「一键安装依赖」上**静默失效**：`requirements.txt` 按
    `pymupdf4llm_plugin/` 去找、找不到 → 接口返回 `no-requirements` → 前端
    **根本不显示安装按钮**。而插件本身照样能加载（command 是对的），所以从
    界面上看是「这个插件不需要依赖」。模板也因此一直没被发现。
    """
    plugin = getattr(adapter, "_plugin", None)
    for part in getattr(plugin, "command", None) or []:
        p = Path(part)
        if p.is_absolute() or len(p.parts) < 2:
            continue
        if any(seg in (".venv", "venv") for seg in p.parts):
            continue          # `xxx/.venv/Scripts/python.exe` —— 解释器，不是脚本
        return adapter._plugin_dir / p.parent

    # 回退：声明文件 stem（name 可与文件名不同，如 pymupdf4llm_heavy.py 里
    # name=pymupdf4llm，子目录却是 pymupdf4llm_heavy/）
    stem = adapter._source_file.stem if adapter._source_file else adapter.name
    return adapter._plugin_dir / stem


def _venv_python(pkg_dir: Path) -> Path:
    """venv 内 python 可执行文件路径（平台相关）"""
    if os.name == "nt":
        return pkg_dir / ".venv" / "Scripts" / "python.exe"
    return pkg_dir / ".venv" / "bin" / "python3"


def _venv_pip(pkg_dir: Path) -> Path:
    if os.name == "nt":
        return pkg_dir / ".venv" / "Scripts" / "pip.exe"
    return pkg_dir / ".venv" / "bin" / "pip"


def _venv_valid(pkg_dir: Path) -> bool:
    """venv 是否可用 —— 判据唯一实现在 `core.deps.probe_venv`（含 base python 校验）。

    ⚠️ **不要在这里再读一遍 `pyvenv.cfg`**：本函数与插件声明文件里的
    `precheck()`（`probe_venv`）回答的是同一个问题。两份实现的失效形态是
    「设置页说依赖已装、自检说没装」——同一个 venv，两个答案，而两边都不报错。

    历史（判据搬走前记在这里）：2026-08-06 修复失效 venv（uv 管理的 python 被
    升级/清理 → "No Python at ..."）视为未安装；2026-08-14 修复 WinError 448
    （不受信任装入点）→ 视为不可用，避免冒到 `/api/plugins` 变成 500。
    """
    venv = pkg_dir / ".venv"
    reason = probe_venv(venv)
    # 「压根没建」是常态（用户还没点一键安装），不打日志；「建过却不可用」才值得记
    if reason and venv.is_dir():
        logger.warning("Plugin %s venv 不可用（视为未安装）: %s", pkg_dir.name, reason)
    return reason is None


def _deps_installed(adapter) -> bool:
    """依赖是否已安装（venv python 存在且 base python 有效；失效 venv 视为未装）"""
    return _venv_valid(_plugin_subdir(adapter))


def _has_requirements(adapter) -> bool:
    """插件子目录是否存在 requirements.txt（进程外插件一键安装的前提）"""
    return (_plugin_subdir(adapter) / "requirements.txt").exists()


def _resolve_venv_base_python() -> Optional[str]:
    """建 venv 用的 base python 路径。

    源码运行 → 当前后端 python（sys.executable，版本与后端一致）。
    PyInstaller 打包 → sys.executable 是主程序 exe（不支持 -m venv），改试系统 python：
      Windows 优先 py launcher（官方安装自带），次选 python——均用 `-V` 验证可执行，
      防 Microsoft Store 别名（假 python.exe 启动会打开商店，建出损坏 venv）。
    都不可用 → None（调用方降级引导，不静默失败）。
    """
    if not getattr(sys, "frozen", False):
        return sys.executable
    candidates = ["py", "python"] if os.name == "nt" else ["python3", "python"]
    for cand in candidates:
        path = shutil.which(cand)
        if not path:
            continue
        try:
            subprocess.run(
                [path, "-V"], check=True, capture_output=True, text=True, encoding="utf-8",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            return path
        except (subprocess.CalledProcessError, OSError):
            continue
    return None


def install_plugin_deps(name: str) -> dict:
    """一键安装插件依赖：建 venv + pip install（国内镜像源）。

    耗时操作（建 venv + 下载安装），调用方用 asyncio.to_thread 放线程池。
    失败显式化：返回 {status: "error", error: 原因}，不静默降级。
    """
    from app.core.errors import AppError

    entry = PLUGIN_REGISTRY.get(name)
    if not entry:
        raise AppError("PLUGIN_NOT_FOUND", f"插件不存在: {name}")
    adapter = entry["adapter"]
    pkg_dir = _plugin_subdir(adapter)
    req = pkg_dir / "requirements.txt"
    if not req.exists():
        return {"status": "no-requirements", "python": ""}

    venv_py = _venv_python(pkg_dir)
    pip = _venv_pip(pkg_dir)

    try:
        # 1. 建 venv（base python 解析：源码=当前后端 / 打包=系统 python）
        if not _venv_valid(pkg_dir):
            # 失效 venv（base python 不可用，如 uv 管理的 python 被清理）→ 删除重建，
            # 用当前可用的 base python，避免「No Python at ...」无法自愈
            if venv_py.exists():
                import shutil
                shutil.rmtree(pkg_dir / ".venv", ignore_errors=True)
                logger.warning("Plugin %s: 失效 venv（base python 不可用）已删除，重建中", name)
            base_py = _resolve_venv_base_python()
            if not base_py:
                return {
                    "status": "no-python", "python": "",
                    "error": "未检测到可用的 Python，无法自动创建虚拟环境",
                    "hint": ("请先安装 Python（python.org/downloads），或手动执行：\n"
                             "cd ~/.limo/plugins/{name} && python -m venv .venv && "
                             ".venv/Scripts/pip install -r requirements.txt".format(name=name)),
                }
            logger.info("Plugin %s: creating venv (base=%s) at %s", name, base_py, pkg_dir / ".venv")
            subprocess.run(
                [base_py, "-m", "venv", str(pkg_dir / ".venv")],
                check=True, capture_output=True, text=True, encoding="utf-8",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        # 2. pip install（镜像源）
        logger.info("Plugin %s: pip install -r %s (mirror=%s)", name, req.name, PIP_MIRROR)
        proc = subprocess.run(
            [str(pip), "install", "-i", PIP_MIRROR, "-r", str(req)],
            capture_output=True, text=True, encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip()[-500:]
            logger.warning("Plugin %s deps install failed: %s", name, tail[:200])
            return {"status": "error", "python": str(venv_py),
                    "error": tail or "pip install 失败，请查看日志"}
        logger.info("Plugin %s deps installed via %s", name, str(venv_py))
        return {"status": "ok", "python": str(venv_py)}
    except Exception as e:
        logger.warning("Plugin %s deps install exception: %s", name, e)
        return {"status": "error", "python": str(venv_py), "error": str(e)[:300]}


def set_plugin_config(name: str, config: dict) -> None:
    """更新插件配置到内存（下次执行 stdin 即含新 config；落盘由 router 负责）"""
    entry = PLUGIN_REGISTRY.get(name)
    if not entry:
        return
    adapter = entry.get("adapter")
    if adapter is None:
        # 被禁用的插件没有 adapter（代码未加载，P1-02）。配置**已由 router 落盘**，
        # 重启加载时会被 `init_plugins` 回填 —— 这里无需（也不能）更新内存。
        return
    adapter.plugin_config = dict(config)


def set_plugin_enabled(name: str, enabled: bool) -> None:
    """同步插件启用状态到内存（enable/disable 路由调用）。

    枚举结果立即反映期望状态（前端开关即时翻转）；引擎注册需重启生效。
    config 落盘由 router 负责。
    """
    entry = PLUGIN_REGISTRY.get(name)
    if entry:
        entry["enabled"] = bool(enabled)


def enumerate_plugins() -> list[dict]:
    """枚举所有已发现插件（含门控拒绝/禁用的），按 name 排序。

    返回管理 API 友好的 dict 列表（前端按 category 分组渲染）。

    ⚠️ 被 `plugin_states` 禁用的插件**没有 adapter**（P1-02：整个文件没执行，见
    `init_plugins`）——能力 / schema / 依赖状态一律给空值：那些信息只能在执行过模块
    之后才知道，而这条路径的全部意义**就是不执行它**。保住的是那条闭环：
    它仍出现在列表里、能点「启用」、重启后恢复正常信息。
    """
    result = []
    for name, entry in PLUGIN_REGISTRY.items():
        adapter = entry.get("adapter")
        if adapter is None:
            result.append({
                "name": name,
                "namespace": "engine",
                "category": entry.get("category", ""),
                "displayName": name,
                "userDesc": "",
                "license": "",
                "fileTypes": [],
                "configSchema": [],
                "path": entry.get("path", ""),
                # enabled / pendingRestart 仍走同一套语义：点「启用」后应为
                # enabled=True + pendingRestart=True（重启才真正加载）
                "enabled": entry.get("enabled", False),
                "loaded": entry.get("loaded", False),
                "pendingRestart": entry.get("enabled", False) != entry.get("loaded", False),
                "config": {},
                "reason": entry.get("reason", ""),
                "execMode": None,
                "hasRequirements": False,
                "depsInstalled": False,
                # `None` = **无法判断**（代码没加载，自检也就无从调用）——
                # 与 `False`（自检说不可用）刻意分开：前端只渲染 false，
                # 「问不到」不该长成一条点不动的「不可用」。
                "ready": None,
                "precheckReason": "",
            })
            continue
        # 自检（`EnginePlugin.precheck`）。放在这里而不是路由里：它是插件卡片
        # 「为什么用不了」的唯一数据源，任何枚举方（含将来的 CLI）都该拿到同一份。
        precheck_reason = adapter.precheck() or ""
        result.append({
            "name": name,
            "namespace": adapter.namespace,
            "category": adapter._plugin.category,
            "displayName": adapter.display_name,
            "userDesc": adapter.user_desc,
            "license": adapter.license,
            "fileTypes": adapter.file_types,
            "configSchema": adapter.config_schema,
            "path": entry.get("path", ""),
            "enabled": entry.get("enabled", False),
            # loaded = 运行时是否已注册；enabled != loaded → 需重启才生效（前端显示待重启徽章）
            "loaded": entry.get("loaded", False),
            "pendingRestart": entry.get("enabled", False) != entry.get("loaded", False),
            "config": adapter.plugin_config,
            "reason": entry.get("reason", ""),
            "execMode": adapter._exec_mode,
            "ready": precheck_reason == "",
            "precheckReason": precheck_reason,
            # 依赖一键安装状态（进程外插件）：requirements.txt 存在？venv 已建？
            "hasRequirements": _has_requirements(adapter),
            "depsInstalled": _deps_installed(adapter),
        })
    result.sort(key=lambda p: (p["category"], p["name"]))
    return result


def uninstall_plugin(name: str, cfg: dict) -> list[str]:
    """卸载插件：删除 {data_dir}/plugins/ 下同名 .py + 同名目录（含 venv/模型）。

    路径穿越防护：resolve() 后校验在插件目录内，杜绝 ../ 与绝对路径逃逸。
    v1 单目录下插件全在 ~/.limo/plugins/，统一路径校验即可，无 source 区分。
    Windows 删除运行中 venv 可能占用失败 → 调用方捕获异常给前端反馈。
    """
    from app.core.errors import AppError

    entry = PLUGIN_REGISTRY.get(name)
    if not entry:
        raise AppError("PLUGIN_NOT_FOUND", f"插件不存在: {name}")

    user_dir = Path(cfg.get("data_dir", "")) / "plugins"
    base = user_dir.resolve()

    removed = []
    for p in [user_dir / f"{name}.py", user_dir / name]:
        if not p.exists():
            continue
        target = p.resolve()
        if base != target and base not in target.parents:
            raise AppError("PLUGIN_PATH_OUTSIDE", f"插件路径越界，拒绝删除: {target}")
        if p.is_dir():
            import shutil
            shutil.rmtree(p)
        else:
            p.unlink(missing_ok=True)
        removed.append(str(p))

    PLUGIN_REGISTRY.pop(name, None)
    return removed
