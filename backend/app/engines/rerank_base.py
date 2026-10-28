"""重排引擎协议 —— 「一条查询 + N 段候选文本 → 一个（下标, 分数）的表」。

与 `embedding_base.EmbeddingEngine` 并列的**第三类引擎契约**（前两类是
`core.protocols.Engine` 的「一个文件 → 一段文本」与 embedding 的
「一组文本 → 一组向量」）。

## 三条契约（逐条有测试）

1. **返回 `[(原始下标, 分数)]`**，下标必须在 `[0, len(documents))` 内且
   **互不重复**。重复下标是契约破裂 —— 「同一段文本出现两次」在重排里没有
   任何语义，它只可能意味着引擎把下标算错了，而算错的下标会让**别的文档的
   分数**贴到这篇头上（顺序看起来完全正常）。故：**越界与重复都直接抛错**。
2. **空输入返回 `[]`，且不发起任何调用**。省的不是钱（本来也没内容可算），
   是「一条必失败的请求」——云端会为它返回 400，本地会白跑一次模型加载。
3. **允许返回子集**。引擎可能只返回 `top_n` 条（`max_documents` 之外还有
   服务端自己的截断）；调用方负责把缺失的下标按原序追加回去。**排在此处
   而不是引擎里**：谁提供候选谁就该负责「一个都不能丢」，否则一次重排会
   静默缩短结果列表。

调用方的排序算子（稳定排序、同分保留输入序）**不在这里** —— 它住在
`services/rerank_search.py`，与 `core/vector_index.rrf` 的分线一致：
协议只管形状，算子只管算法。

## 刻意不放协议里的字段（一律 `getattr`）

- `dimension`：rerank **没有**维度这个概念，从 embedding 抄过来就是一个永远
  为 0 的死字段，而读它的代码会以为它有意义。
- `capabilities`：不进 `engineOptions`，没有任何消费方。
- `precheck()` / `warmup()` / `release()`：与 embedding 同一个理由 ——
  `runtime_checkable` 的 Protocol **加一个成员就让所有云端引擎不再满足契约**
  （云端没有「载入」这个概念）。消费方一律 `getattr(engine, "warmup", None)`，
  `None` 的含义是「视为就绪 / 无需预热」，不是「未知」。
- `is_local` / `display_name`：这两个**确实要有**（设置页要区分「本机跑」
  与「按量计费」），但它们属于**展示层属性**，与 embedding 同款地放在类上、
  用 `getattr(engine, "display_name", engine.name)` 读 —— 协议不约束它们，
  不会因为缺一个就判定引擎不合法。
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class RerankEngine(Protocol):
    """重排引擎。实现者见 `rerank_dashscope.DashScopeRerankEngine` 与插件适配器。"""

    #: 引擎名（`engine_bindings` / 设置页下拉里的 key，如 `dashscope-rerank`）
    name: str

    #: 模型标识（写进日志与状态页，如 `gte-rerank-v2`）
    model_id: str

    #: 单次请求最多接收多少段文档。**0 = 不设上限**。
    #: 调用方按它截断候选（超了服务端会 400，而不是「自动分批」）。
    max_documents: int

    async def rerank(self, query: str,
                     documents: list[str]) -> list[tuple[int, float]]:
        """给每段候选打一个相关性分，**分数越大越相关**。

        返回 `[(下标, 分数)]`，下标指向 `documents` 里的位置（不是重排后的
        新位置）。条数可以少于入参（见契约 3），但下标必须合法且不重复。
        """
        ...
