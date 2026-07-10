"""
适配器注册表 + 引擎注册表
参照 system-design-spec.md 三.5 + 三.6
"""

from typing import Optional

from .protocols import PipelineAdapter, Engine
from .types import PipelineInput


# -- 适配器注册表 --

class AdapterRegistry:
    _adapters: list[PipelineAdapter] = []

    @classmethod
    def register(cls, adapter: PipelineAdapter):
        """新增管道只需 register() 即生效"""
        cls._adapters.append(adapter)

    @classmethod
    def route(cls, input: PipelineInput) -> Optional[PipelineAdapter]:
        """找到第一个 accept 返回 True 的适配器"""
        for adapter in cls._adapters:
            if adapter.accept(input):
                return adapter
        return None

    @classmethod
    def all(cls) -> list[PipelineAdapter]:
        return cls._adapters


# -- 引擎注册表 --

class EngineRegistry:
    """按类型分组，每引擎可声明 fallback"""

    _stores: dict[str, dict[str, Engine]] = {}

    @classmethod
    def register(cls, category: str, name: str, engine: Engine):
        if category not in cls._stores:
            cls._stores[category] = {}
        cls._stores[category][name] = engine

    @classmethod
    def get(cls, category: str, name: str) -> Optional[Engine]:
        return cls._stores.get(category, {}).get(name)

    @classmethod
    def list_category(cls, category: str) -> list[str]:
        return list(cls._stores.get(category, {}).keys())
