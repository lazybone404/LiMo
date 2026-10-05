"""
适配器注册表 + 引擎注册表
参照 system-design-spec.md 三.5 + 三.6
"""

from typing import Optional

from .protocols import PipelineAdapter, Engine
from .types import PipelineInput


# -- 引擎类别清单 --

# 引擎类别的**唯一真相源**（`asr` / `ocr` / `doc`）。
#
# ⚠️ **两个视图并存是刻意的，不要合并成一个容器**：set 与 tuple 的语义不同 ——
#   - `ENGINE_CATEGORIES`（frozenset）：**成员判据**（`if cat not in ...: continue`），
#     只需要「在不在」；
#   - `ENGINE_CATEGORY_ORDER`（tuple）：**遍历顺序**，`GET /engine-registry`
#     按它分组输出，前端引擎选择器的分组顺序由它决定。
#   合并成一个就得在遍历处补 `sorted()`，而排序依据从「刻意的业务顺序」变成
#   「字典序」——那是一次没人会注意到的界面顺序变更。
#
# ⚠️ 收敛前这三处（`routers/capabilities.py` 两处 + `core/config.py` 一处）各写一份，
#   且**其中一处是 list 形态**（语义已经分叉）。新增类别时漏改任一处的症状是
#   「该类别在设置页看不见」或「分组顺序变了」，**两处都不报错**（模式 #54）。
ENGINE_CATEGORIES: frozenset[str] = frozenset({"asr", "ocr", "doc"})
ENGINE_CATEGORY_ORDER: tuple[str, ...] = ("asr", "ocr", "doc")


# -- 适配器注册表 --

class AdapterRegistry:
    # 存**类**而非实例 — 单例实例的 cookies/_engine_config/cancel_event 会被并发请求互相覆盖
    _adapters: list[type] = []

    @classmethod
    def register(cls, adapter_cls: type):
        """新增管道只需 register() 即生效（传适配器类，route 时实例化）"""
        cls._adapters.append(adapter_cls)

    @classmethod
    def route(cls, input: PipelineInput) -> Optional[PipelineAdapter]:
        """找到第一个 accept 返回 True 的适配器，返回**新实例**（一请求一实例，无共享状态）"""
        for adapter_cls in cls._adapters:
            # accept 是纯输入判断（无实例状态依赖），命中前才实例化
            inst = adapter_cls()
            if inst.accept(input):
                return inst
        return None

    @classmethod
    def all(cls) -> list[PipelineAdapter]:
        return [c() for c in cls._adapters]


# -- 引擎注册表 --
# 统一注册表 + 类别命名空间：EngineRegistry 按 category 分组（doc/ocr/asr…），
# 未来 adapter:video / tool:search 命名空间插件到达时再扩展键空间（v1 仅 engine 命名空间生效）。

class EngineRegistry:
    """按类型分组（无引擎降级链：失败直接报错）"""

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
