"""
引擎插件加载器 — 从 {data_dir}/plugins/*.py 自动发现并注册第三方引擎。

支持两种模式：
  进程内（默认）: 直接 import，依赖主 venv。适合简单插件。
  进程外（command 字段）: subprocess 调用独立 venv，依赖隔离。适合重依赖/协议敏感插件。

MVP 范围（个人自用，不防恶意代码）：
  - 扫描 ~/.limo/plugins/ 顶层 *.py
  - import 找到 EnginePlugin 子类 → 注册到 EngineRegistry
  - command 字段 → 走 subprocess，自带 venv
  - 许可证字段用于前端显示（黄色=AGPL/GPL, 灰色=安全）

不做的（等有第三方开发者时再评估）：
  - pip 依赖自动安装（插件 README 写"手动 pip install"）
  - 沙箱隔离
  - 签名验证
"""

import importlib.util
import json
import logging
import subprocess
from pathlib import Path
from typing import Optional

from app.core.registry import EngineRegistry
from app.core.protocols import Engine

logger = logging.getLogger(__name__)


class EnginePlugin:
    """插件基类 — 第三方引擎只需继承此类并实现 process()。

    例 (~/.limo/plugins/my_ocr.py):
        from app.engines.plugin_loader import EnginePlugin
        class MyOCREngine(EnginePlugin):
            name = "my-ocr"
            category = "ocr"
            license = "Apache-2.0"
            def process(self, file_path, config):
                import my_ocr_lib
                return my_ocr_lib.extract(file_path)
    """

    # ── 插件元数据（子类必须覆盖） ──

    name: str = ""          # 引擎名，注册到 EngineRegistry 的 key
    category: str = "doc"   # "asr" | "ocr" | "doc"
    license: str = "Unknown"  # SPDX 标识：Apache-2.0 / MIT / AGPL-3.0 / GPL-3.0 / Proprietary
    description: str = ""
    file_types: list[str] = []  # 支持的文件扩展名，如 [".pdf", ".docx"]

    # ── 可选 ──

    fallback: Optional[Engine] = None

    # ── 进程外模式 ──

    command: Optional[list[str]] = None
    # 进程外模式：相对于插件 .py 所在目录的路径（cwd 即 plugins/）
    # 例：["pymupdf4llm_heavy/.venv/Scripts/python.exe", "pymupdf4llm_heavy/engine.py"]
    # 设为 None → 进程内模式（默认），走 process()

    timeout: int = 300  # 子进程超时秒数（仅进程外模式）

    # ── Engine 协议适配 ──

    def accept(self, file_path: str) -> bool:
        if not self.file_types:
            return True
        return any(file_path.lower().endswith(ext) for ext in self.file_types)

    def process(self, file_path: str, config: dict) -> str:
        raise NotImplementedError


# ── Plugin adapter: wraps EnginePlugin into Engine protocol ──

class _PluginEngineAdapter:
    """将 EnginePlugin 适配为 EngineRegistry 兼容的 Engine 协议。
    Engine.execute 签名为 (*args, **kwargs)，取第一个位置参数为 file_path，
    其余 kwargs（media_dir, cancel_event 等）合并进 config 传给插件。

    支持双模式：
      - 进程内（command=None）：直接调用 plugin.process()
      - 进程外（command 非空）：subprocess 调用独立 venv
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

    def accept(self, file_path: str) -> bool:
        return self._plugin.accept(file_path)

    def run(self, file_path: str, **kwargs) -> str:
        """Engine 协议入口 — 带降级（当前直接委托 execute）"""
        return self.execute(file_path, **kwargs)

    def execute(self, file_path: str, **kwargs) -> str:
        if self._plugin.command:
            return self._run_external(file_path, kwargs)
        return self._plugin.process(file_path, kwargs)

    def _run_external(self, file_path: str, config: dict) -> str:
        """进程外执行：subprocess 调用独立 venv，stdin 传 JSON config"""
        # 过滤不可 JSON 序列化的值（cancel_event 等）
        safe = {
            k: v for k, v in config.items()
            if isinstance(v, (str, int, float, bool, list, dict, type(None)))
        }
        # command 相对路径 → 基于插件目录解析为绝对路径
        # Windows CreateProcess 对带子目录的相对路径参数解析不可靠
        cmd = [
            str(self._plugin_dir / part) if not Path(part).is_absolute() else part
            for part in self._plugin.command
        ]
        try:
            result = subprocess.run(
                cmd + ["--input", file_path],
                input=json.dumps(safe),
                capture_output=True, text=True,
                encoding="utf-8",
                cwd=str(self._plugin_dir),
                timeout=self._plugin.timeout,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"Plugin {self.name} timed out after {self._plugin.timeout}s"
            )
        if result.returncode != 0:
            raise RuntimeError(
                result.stderr.strip() or f"Plugin exited with code {result.returncode}"
            )
        return result.stdout


# ── 加载入口 ──

def init_plugins(config: dict):
    """扫描 {data_dir}/plugins/ 目录，加载所有 .py 插件。

    在 init_engines() 之后调用，确保内置引擎已注册。
    插件覆盖规则：如果同 category + name 已有内置引擎，插件覆盖它（用户优先）。
    """
    plugins_dir = Path(config.get("data_dir", "")) / "plugins"
    if not plugins_dir.exists():
        return

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
                    try:
                        instance = obj()
                    except Exception as e:
                        logger.warning(
                            "Plugin %s instantiation failed: %s", py_file.name, e
                        )
                        continue

                    adapter = _PluginEngineAdapter(instance, py_file.parent)
                    EngineRegistry.register(instance.category, instance.name, adapter)
                    logger.info(
                        "Plugin loaded: %s/%s (%s) [%s]",
                        instance.category,
                        instance.name,
                        instance.license,
                        py_file.name,
                    )
                    found = True
                    loaded += 1

            if not found:
                logger.debug("No EnginePlugin subclass found in %s", py_file.name)

        except Exception as e:
            logger.warning("Plugin %s failed to load: %s", py_file.name, e)

    if loaded > 0:
        logger.info("Plugin loader: %d plugin(s) loaded from %s", loaded, plugins_dir)
