"""
产物注册表 — 定义所有可生成的产物类型及其依赖关系。

加新产物只需在此注册，无需改动生成分发逻辑或前端 UI。
"""

from dataclasses import dataclass, field


@dataclass
class ProductDef:
    name: str            # 产物标识，也是 mode 参数值
    label: str           # 中文标签（前端复选框文字）
    category: str        # "core"（基础产物，从原文生成）| "衍生"（从其他产物生成）
    inputs: list[str]    # 依赖的产物名列表，空列表 = 无依赖（可从原文直接生成）
    description: str     # 说明文字（前端 tooltip）
    requires_llm: bool = True  # 是否需要 LLM，false 表示纯本地计算（如 mindmap 数据）


# ── 注册表 ──

PRODUCTS: dict[str, ProductDef] = {
    "notes": ProductDef(
        name="notes",
        label="笔记",
        category="core",
        inputs=[],
        description="结构化笔记：提取关键信息，组织为层次清晰的 Markdown",
    ),
    "polished": ProductDef(
        name="polished",
        label="整理版原文",
        category="core",
        inputs=[],
        description="整理版原文：去噪音、修正错别字、合并段落，保留完整信息",
    ),
    "fact_check": ProductDef(
        name="fact_check",
        label="事实验证",
        category="衍生",
        inputs=["notes"],
        description="对关键事实断言进行网络交叉验证，返回可信度标注和来源引用",
    ),
}

# 为了减少 GC 和快速查找
_PRODUCT_NAMES = frozenset(PRODUCTS.keys())


def get_product(name: str) -> ProductDef | None:
    """查找产物定义"""
    return PRODUCTS.get(name)


def resolve_order(requested: list[str]) -> list[list[str]]:
    """拓扑排序，返回执行批次。同批次内可并行，批次间必须串行。

    ["notes", "polished", "fact_check"] → [["notes","polished"], ["fact_check"]]
    ["fact_check"] → [["notes"], ["fact_check"]]  （自动补齐依赖）
    """
    if not requested:
        return []

    # 过滤无效产物名
    valid = [p for p in requested if p in _PRODUCT_NAMES]
    if not valid:
        return []

    # 自动补齐依赖：如果用户请求了 fact_check 但没有 notes，
    # 检查是否有已有的 notes 产物可用（由调用方判断），
    # 这里只做拓扑排序，补齐逻辑由 generate.py 处理
    seen: set[str] = set()
    batches: list[list[str]] = []
    remaining = set(valid)

    # BFS 拓扑排序
    while remaining:
        batch: list[str] = []
        for name in list(remaining):
            prod = PRODUCTS[name]
            if all(inp in seen for inp in prod.inputs):
                batch.append(name)
        if not batch:
            # 循环依赖或无法满足的依赖 → 剩余的直接排到最后
            batches.append(list(remaining))
            break
        for name in batch:
            remaining.remove(name)
            seen.add(name)
        batches.append(batch)

    return batches


def to_capability_list() -> list[dict]:
    """转为前端 /capabilities 需要的格式"""
    return [
        {
            "name": p.name,
            "label": p.label,
            "category": p.category,
            "inputs": p.inputs,
            "description": p.description,
        }
        for p in PRODUCTS.values()
    ]
