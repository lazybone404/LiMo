"""
引擎插件加载器 — 从 {data_dir}/plugins/*.py 自动发现并注册第三方引擎。

MVP 范围（个人自用，不防恶意代码）：
  - 扫描 ~/.limo/plugins/ 目录
  - import 每个 .py 文件
  - 找到 EnginePlugin 子类 → 注册到 EngineRegistry
  - 许可证字段用于前端显示（黄色=AGPL/GPL, 灰色=安全）

不做的（等有第三方开发者时再评估）：
  - pip 依赖自动安装（插件 README 写"手动 pip install"）
  - 沙箱隔离
  - 签名验证
"""

import importlib.util
import logging
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
    Engine.execute 签名为 (*args, **kwargs)，我们取第一个位置参数为 file_path，
    其余 kwargs（media_dir, cancel_event 等）合并进 config 传给插件。
    """

    def __init__(self, plugin: EnginePlugin):
        self._plugin = plugin
        self.name = plugin.name
        self.license = plugin.license
        self.description = plugin.description
        # 从插件复制必要的 Engine 属性
        self.supported_modes = getattr(plugin, 'supported_modes', [])
        self.capabilities = getattr(plugin, 'capabilities', [])

    def accept(self, file_path: str) -> bool:
        return self._plugin.accept(file_path)

    def execute(self, file_path: str, **kwargs) -> str:
        """适配：将 media_dir 等 kwargs 合并到 config 传给 plugin.process()"""
        return self._plugin.process(file_path, kwargs)


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
    for py_file in sorted(plugins_dir.glob("*.py")):
        try:
            spec = importlib.util.spec_from_file_location(
                f"_limo_plugin_{py_file.stem}", py_file
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

                    adapter = _PluginEngineAdapter(instance)
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
