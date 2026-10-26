"""查询融合 —— 字面（FTS5）∪ 语义（向量）→ RRF 重排。

设计依据 [docs/search-retrieval.md](../../../docs/search-retrieval.md) §3.3 / §4.3。
**算子一个都不在这里**：切块、分组排名、RRF 全在 `core/vector_index.py`
（产品与评测 import 同一份，这是唯一目的）。本模块只做「取数 → 调算子 → 组装」。

## 失败一律显式，不静默退化

查询 embedding 失败 / 超时 / 索引口径不符 / 库不可用 → **返回 error 文案，字面结果照给**。
不静默退化的理由：用户看到「搜得到、但好像没变聪明」时，无法区分
「向量没帮上忙」与「向量压根没跑」——而这两件事的处置完全不同（前者等索引建完，
后者去查 Key / 开关 / 口径）。同理 `hits` 数字**必须**留在返回值里：它是
「开了但索引还空」与「压根没开」唯一的区分点（同 `webSearch.hits: 0` 的取舍）。

## 刻意不做

- **不做分数归一化**：BM25 与余弦量纲不可比，任何归一化都引入一个新超参；
  RRF 只用排名，一个数都不碰。代价是它**只能重排两路都召回了的文档** ——
  一路召不回的正确文档救不了，那是 rerank 的地盘，不是 RRF 的。
- **不做矩阵缓存**：见 `VectorStore.load_matrix` 的注释（`count`+`max(rowid)`
  这个看似自然的键会因 rowid 复用而失效）。
- **不接 CLI**：`QueryService.search_resources` 保持同步纯字面（CLI 链路全同步），
  融合发生在 HTTP 层的异步编排里。于是「开关打开时 `limo search` 仍是纯字面」
  是本条落地的**已知边界**，不是遗漏 —— 写进 AGENTS.md。
"""

from __future__ import annotations

import asyncio

import numpy as np

from app.core import logging as log
from app.core.vector_index import rrf, vector_rank_scored
from app.core.vector_store import DEFAULT_CHUNK_SIZE

#: 两路各取多少条进融合。`limit` 更大时跟随 `limit` —— 否则「取 100 条」会被这里
#: 悄悄砍回 50，而前端 `SEARCH_LIMIT=100` 正是「大列表分批渲染」的下限需求。
DEFAULT_DEPTH = 50

#: RRF 的 k。29 题实测 `10` 优于 `60`（见 docs/search-retrieval.md §3.3）。
RRF_K = 10

#: 查询 embedding 的超时（秒）。单条短文本一次往返，正常 <500ms；3s 已经很宽松，
#: 再长就是「用户以为搜索卡住了」而不是「网络慢」。
QUERY_TIMEOUT = 3.0

#: **模型载入**的超时（秒），与 `QUERY_TIMEOUT` 刻意分成两个常量 —— 它们管的不是
#: 同一件事：一个是「一条短文本算得多慢」，一个是「把 22.8MB 权重读进内存要多久」
#: （实测 3–5 秒，慢盘 / 首次冷读可能更久）。
#:
#: ⚠️ **不合并、也不放宽 `QUERY_TIMEOUT`**：模型被释放后（`search.vector_release`）
#: 第一次查询必须先加载，而加载 3–5 秒 > 3.0 秒。共用一个超时的后果是**那次查询
#: 必定超时并退回纯字面**，提示还写着「语义检索超时」—— 读起来像网络问题，而本地
#: 模型根本不联网。把载入单独拎出来，冷查询是「慢一次」，而不是「静默失效」。
LOAD_TIMEOUT = 30.0


class VectorSearcher:
    """把一条查询拿去和全库向量比，再与字面结果做 RRF。

    ⚠️ **构造即代表「开关已开」**：调用方（`dependencies.get_query_service`）只在
    `search.vector_enabled` 为真且引擎可用时才创建它，所以本类不必再读配置 ——
    两份开关判据会在「刚在设置里关掉、下一次请求还没进来」这类时刻分叉。
    """

    def __init__(self, vec, engine, *, chunk_size: int = DEFAULT_CHUNK_SIZE,
                 timeout: float = QUERY_TIMEOUT, load_timeout: float = LOAD_TIMEOUT):
        if engine is None:
            raise ValueError("VectorSearcher 需要一个 embedding 引擎")
        self._vec = vec
        self._engine = engine
        # `chunk_size` **不参与查询计算**（查询是一个短字符串，不切块），它只用于
        # 口径校验 —— 四元组是整体身份，任一不符即整体不可用（§六）。默认值与
        # worker 同源，故这个参数只在将来真的改切块规则时才需要显式传。
        self._chunk_size = int(chunk_size)
        self._timeout = float(timeout)
        self._load_timeout = float(load_timeout)

    async def fuse(self, query: str, lexical_ids: list[str], *,
                   limit: int, depth: int | None = None) -> tuple[list[str], dict]:
        """融合两路 → `(id 顺序, semantic 自证段)`。

        `semantic` 的形状固定为 `{"error": str | None, "hits": int}`：
        `hits` 是**本次真正参与融合的语义命中篇数**（`vector_rank_scored` 按 `depth`
        取，所以上限就是 `depth`，**不是全库命中总数**）。它恒在，不看 `error` 也要读它 ——
        `hits == 0` 且 `error is None` 的含义是「跑通了，但库里还没有可比的向量」，
        与「没开」是两件事。
        """
        depth = int(depth or DEFAULT_DEPTH)
        lexical_ids = list(lexical_ids)
        semantic: dict = {"error": None, "hits": 0}

        reason = self._precheck()
        if reason:
            semantic["error"] = reason
            return lexical_ids[:limit], semantic

        reason = await self._ensure_loaded()
        if reason:
            semantic["error"] = reason
            return lexical_ids[:limit], semantic

        try:
            qv = await self._embed_query(query)
        except asyncio.TimeoutError:
            semantic["error"] = f"语义检索超时（>{self._timeout:.0f}s），本次仅用字面结果"
            log.warning("查询 embedding 超时", timeout=self._timeout)
            return lexical_ids[:limit], semantic
        except Exception as e:  # noqa: BLE001 —— 任何编码失败都只降级这一次查询
            semantic["error"] = f"语义检索暂不可用（{type(e).__name__}），本次仅用字面结果"
            log.warning("查询 embedding 失败", err=f"{type(e).__name__}: {e}"[:200])
            return lexical_ids[:limit], semantic

        try:
            # `load_matrix` 是同步读盘（几十到几百毫秒）：放在事件循环里会让这一整个
            # 请求周期内的其它协程（worker 的 await、SSE 心跳）一起停住。
            loaded = await asyncio.to_thread(self._vec.load_matrix)
        except Exception as e:  # noqa: BLE001
            semantic["error"] = "语义检索暂不可用（读向量索引失败），本次仅用字面结果"
            log.warning("读取向量矩阵失败", err=f"{type(e).__name__}: {e}"[:200])
            return lexical_ids[:limit], semantic
        if loaded is None:
            return lexical_ids[:limit], semantic          # 库空：hits=0 / error=None

        mat, owner = loaded
        vector_ids = vector_rank_scored(mat, owner, [qv], depth=depth)[0]
        semantic["hits"] = len(vector_ids)
        return rrf([lexical_ids, [tid for tid, _ in vector_ids]], k=RRF_K)[:limit], semantic

    # ── 前置校验 ──

    def _precheck(self) -> str | None:
        """返回**给用户看的原因**（None = 可以跑）。

        四档的理由是「用户能采取的行动」不同（§4.3 的分类粒度）：
        库坏 → 无自助手段；没建过 → 等 worker / 去看开关；口径不符 → 去重建；
        能跑 → 无事。
        """
        if not self._vec.available:
            return "向量索引库不可用"
        # ⚠️ 这里读两次 identity（都只是单行查询，微秒级）：`read_identity` 用来
        # 区分「没建过」与「口径不符」，`identity_ok` 用来复用**唯一一份**比对判据。
        # 为了省这一次读而在本模块再写一遍四元组比对，就是模式 #54。
        if self._vec.read_identity() is None:
            return "语义索引尚未建立（索引正在后台建立，稍后再试）"
        if not self._vec.identity_ok(self._engine.model_id, self._engine.dimension,
                                     self._chunk_size):
            return "向量索引口径与当前模型不符（需在「设置 → 检索索引」里重建）"
        return None

    async def _ensure_loaded(self) -> str | None:
        """确保模型已在内存里。返回**给用户看的原因**（None = 可以编码）。

        **为什么每次查询都调**：模型可能刚被 `vector_worker.maybe_release` 放掉
        （策略 `idle` / `immediate`），而查询侧无从知道。本地引擎的 `warmup()`
        已加载时是纯内存判断直接返回，云端引擎根本没有这个钩子 —— 两侧都接近零成本，
        所以不需要「是否已加载」这第二份状态（它会和引擎里的 `_inst` 分叉）。

        ⚠️ 超时用 `self._load_timeout` 而**不是** `self._timeout`（见 `LOAD_TIMEOUT`）。
        载入失败/超时的后果只有一次降级，文案必须点明是**载入**而不是「检索超时」——
        后者会把「模型正在读盘」误导成「网络慢」。
        """
        warm = getattr(self._engine, "warmup", None)
        if warm is None:
            return None                        # 云端引擎：模型在服务端，无需载入
        try:
            await asyncio.wait_for(warm(), timeout=self._load_timeout)
        except asyncio.TimeoutError:
            log.warning("语义模型载入超时", timeout=self._load_timeout)
            return (f"语义模型载入超时（>{self._load_timeout:.0f}s），"
                    "本次仅用字面结果")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 —— 载入失败只降级这一次查询
            log.warning("语义模型载入失败", err=f"{type(e).__name__}: {e}"[:200])
            return f"语义模型载入失败（{type(e).__name__}），本次仅用字面结果"
        return None

    async def _embed_query(self, query: str) -> np.ndarray:
        """一条查询 → 单位向量。引擎契约（`engines/embedding_base`）：条数与入参一一对应、
        已 L2 归一化（余弦因此退化成点积）。"""
        vecs = await asyncio.wait_for(self._engine.embed([query]), timeout=self._timeout)
        if not vecs or len(vecs) != 1:
            raise RuntimeError(f"查询编码返回条数不符：返回 {0 if not vecs else len(vecs)} 条")
        v = np.asarray(vecs[0], dtype=np.float32)
        if v.size != int(self._engine.dimension):
            raise RuntimeError(
                f"查询向量维度不符：{v.size} != {self._engine.dimension}")
        return v
