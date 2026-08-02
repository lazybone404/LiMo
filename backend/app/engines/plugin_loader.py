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

import importlib.util
import json
import logging
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

from app.core.registry import EngineRegistry
from app.core.protocols import Engine

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

    def __init__(self, plugin: EnginePlugin, plugin_dir: Path):
        self._plugin = plugin
        self._plugin_dir = plugin_dir
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

def init_plugins(config: dict):
    """扫描 {data_dir}/plugins/ 目录，加载所有插件。

    在 init_engines() 之后调用，确保内置引擎已注册。
    插件覆盖规则：同 category + name 插件覆盖内置引擎（用户优先）。

    门控：command→进程外（默认）；trusted→进程内；无 command 且未 trusted→拒绝（warning）。
    启用：plugin_states[name]==False → 跳过注册但保留 PLUGIN_REGISTRY 条目；
          未配置默认 enabled（向后兼容，现有插件不因新配置而禁用）。
    """
    plugins_dir = Path(config.get("data_dir", "")) / "plugins"
    if not plugins_dir.exists():
        return  # 目录不存在优雅跳过（打包版用户可能从未装插件）

    states = config.get("plugin_states", {}) or {}

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

                    adapter = _PluginEngineAdapter(instance, py_file.parent)

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

def set_plugin_config(name: str, config: dict) -> None:
    """更新插件配置到内存（下次执行 stdin 即含新 config；落盘由 router 负责）"""
    entry = PLUGIN_REGISTRY.get(name)
    if entry:
        entry["adapter"].plugin_config = dict(config)


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
    """
    result = []
    for name, entry in PLUGIN_REGISTRY.items():
        adapter = entry["adapter"]
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
