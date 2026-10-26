"""
适配器注册表 + 引擎注册表
参照 system-design-spec.md 三.5 + 三.6
"""

from typing import Optional

from .protocols import PipelineAdapter, Engine
from .types import PipelineInput


# -- 引擎类别清单 --

# 引擎类别的**唯一真相源**（`asr` / `ocr` / `doc` / `embedding`）。
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
#
# ⚠️ `embedding`（2026-10-26）与前三类**性质不同**：asr / ocr / doc 都是「文件 → 文本」，
#   受 `engine_bindings` 的子场景绑定（`doc:pdf` 之类）管辖；embedding 吃的是**文本**、
#   吐的是**向量**，只有「云端 / 本地」二选一（`search-retrieval.md` §3.1 / §6.1）。
#   仍放进这张表，是因为**发现机制完全一样**（注册 → capabilities 下发 → 前端可见），
#   而另建一张「另一种引擎类别」的表正是模式 #54 的入口。放在 ORDER 末尾：
#   前端各处都按具体 category 取（`engineOptions.doc` / `.asr` / `.ocr`），
#   没有「遍历所有 key 渲染」的消费方，故新增末位对现有界面零影响。
ENGINE_CATEGORIES: frozenset[str] = frozenset({"asr", "ocr", "doc", "embedding"})
ENGINE_CATEGORY_ORDER: tuple[str, ...] = ("asr", "ocr", "doc", "embedding")


# 引擎**能力位**（不是类别）→ 中文标签。经 capabilities 下发，前端引擎下拉里
# 显示的「能力」一栏用它（此前前端把 `capabilities` 原样 join，用户看到裸英文
# `pypdfium2 · doc`）。
#
# ⚠️ **与 `lib/modelCaps.ts` 是两套词汇，不要合并**：那边是**模型能力位**
# （`text`/`image`/`video`/`reasoning`/`document`——「这个模型收得下什么模态」），
# 这边是**引擎能力位**（「这个引擎能从文件里取出什么」）。两边都有 `text` 与
# `image`，但含义与文案都不同（模型 `text`＝文本，引擎 `text`＝文字）。
#
# ⚠️ 新增引擎能力位时**必须同步登记这里**，否则前端徽标退化成裸英文。
# `tests/test_engine_capability_labels.py` 扫全部引擎类的 `capabilities`
# 与这张表做包含断言（模式 #54：同源是搜索题，不能靠记得）。
ENGINE_CAPABILITY_LABELS: dict[str, str] = {
    "text": "文字",
    "table": "表格",
    "image": "图片",
    "ocr": "OCR",
    "pdf": "PDF",
    "office": "文档",
    "asr": "语音",
    # 向量化引擎（2026-10-26）。**引擎侧独有** —— `lib/modelCaps.ts` 的模型能力位
    # 没有它（"这个模型能吐向量"不是用户会分组挑选的维度），故不会撞上那条
    # 「两套词汇各有独有项」的守卫。
    "embedding": "向量",
}


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
