"""向量索引后台 worker —— 分块 → 块级 hash 增量 → 编码 → checkpoint → 失败上限 → 对账。

设计依据 [docs/search-retrieval.md](../../../docs/search-retrieval.md) §4.4–§4.6。
**读写点在别处**：`core/vector_store.py` 只管三表与状态机，本模块只管「谁、按什么顺序、
花几次钱」把 `pending` 变成 `ok`。

## 三条纪律（每一条都对应一种静默失效）

1. **写点只打标记，编码只在这里发生。** 摄入 / 生成完成 / 编辑保存这些路径上一次
   embedding 往返就会把保存按钮卡住几百毫秒，且失败时用户没有任何可采取的动作。
2. **块级 hash 增量，按内容匹配而不是按位置匹配。**
   ⚠️ 这条比 §4.4 的原文更强：位置匹配（`旧[i] vs 新[i]`）在「在开头插一段」时会让
   后面每一块都判「变了」⇒ **整篇重编码**，白花钱；内容匹配（hash → 旧向量）天然免疫
   插入、删除与段落移位，只编码真正变了的文本。
3. **`fail_count` 到上限就真的停。** LiMo 此前所有重试都是免费的（读盘、重解析），
   向量重试**每一次都是计费调用**。所以上限必须落盘（`mark_failed` 负责），
   并且本轮还要有「连续 N 篇失败即停」——单篇超时是偶发，连续超时是网络/Key 出了问题，
   继续跑只是把失败次数均匀地烧在每一篇上。

## 刻意不实现的：document 级 `content_hash` 快速路径

§4.4 曾设想「整篇 hash 没变就跳过」。实测推演表明**它永不命中**：`mark_pending`
会把 `content_hash` 清空，而 worker 只处理 `pending` / `failed` 的文档 —— 也就是说
worker 看到的 `contentHash` **恒为 None**。写了它就是一段永不执行的死代码，还会让人
以为「hash 一致就能跳过」是一条生效中的优化。`content_hash` 仍然写入（状态展示、
将来的批量对账用），但**不读**。真正的增量判据只有块级 `text_hash` 一处。

## 切块算法由 `SCHEMA_VERSION` 表达

四元组里没有「切块算法」字段，改算法必须 bump `vector_store.SCHEMA_VERSION`（切块规则
变了，旧块与新块的向量不可比）。故这里固定用 `chunk_flat`，不接参数 —— 接一个旋钮
等于开了第二条「切块规则可以静默换掉」的路。
"""

from __future__ import annotations

import asyncio

from app.core import logging as log
from app.core.vector_index import chunk_flat
from app.core.vector_store import DEFAULT_CHUNK_SIZE, chunk_hash, pack_vec
from app.generation.doc_text import search_index_text

#: 失败上限（§4.5）。到这里的文档**永久放弃**，由用户在设置页显式重建。
MAX_FAIL = 3

#: 一轮取多少篇。20 篇 × 每篇 1 次 `embed()`（引擎内部再按 10 条分批）≈ 可控的单轮耗时
BATCH_DOCS = 20

#: 队列空时的轮询间隔（秒）。本库的使用节奏是「低频、天级」（§4.5 实测：同 mode 相邻
#: 版本间隔 >1 天占 53%、中位 5 天），所以这里**故意不做 debounce / 积压阈值** ——
#: 那是为秒级编辑频率设计的机制，在这里只会增加状态。
DEFAULT_INTERVAL = 60.0

#: 触发「连续失败」后的冷却（秒）。比 `DEFAULT_INTERVAL` 长：连续失败几乎总是外部原因
#: （Key 过期 / 网络 / 配额），一分钟内自愈的概率很低，而重试是计费的。
RETRY_COOLDOWN = 300.0


class VectorWorker:
    """把 `vec_state` 里的 `pending` 推进到 `ok`。**不持队列、不做超时之外的调度**。"""

    def __init__(self, store, vec, engine, *, chunk_size: int = DEFAULT_CHUNK_SIZE,
                 max_fail: int = MAX_FAIL, batch_docs: int = BATCH_DOCS):
        if engine is None:
            raise ValueError("VectorWorker 需要一个 embedding 引擎")
        self._store = store
        self._vec = vec
        self._engine = engine
        self._chunk_size = int(chunk_size)
        self._max_fail = int(max_fail)
        self._batch_docs = int(batch_docs)

    # ── 一轮 ──

    async def run_once(self) -> dict:
        """把当前队列推到底（或推到失败闸门）。返回 `{indexed, skipped, failed, stopped}`。

        - `indexed` = 真的调了编码的篇数；`skipped` = 块 hash 全中、零调用的篇数
          （空文档也落在这里 —— 它同样「无需编码」，只是块数为 0）
        - `stopped` = 非 None 时表示本轮被闸门中断，值是给用户看的原因
        """
        stats = {"indexed": 0, "skipped": 0, "failed": 0, "stopped": None}
        if not self._vec.available:
            stats["stopped"] = "向量索引库不可用"
            return stats

        attempted: set[str] = set()
        consecutive_failures = 0
        while True:
            ids = self._next_ids(attempted)
            if not ids:
                break
            for tx_id in ids:
                attempted.add(tx_id)
                try:
                    changed = await self._index_one(tx_id)
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001 —— 单篇失败不该中断整轮
                    stats["failed"] += 1
                    consecutive_failures += 1
                    # ⚠️ `mark_failed` 的 `fail_count + 1` 是**花钱的闸门**本身：
                    # 只在内存计数的话重启归零 ⇒ 无限重试 ⇒ 无限烧钱。
                    self._vec.mark_failed(tx_id, f"{type(e).__name__}: {e}")
                    log.warning("向量索引失败", tx=tx_id,
                                err=f"{type(e).__name__}: {e}"[:200],
                                consecutive=consecutive_failures)
                    if consecutive_failures >= self._max_fail:
                        stats["stopped"] = f"连续 {consecutive_failures} 篇编码失败"
                        return stats
                else:
                    consecutive_failures = 0
                    stats["indexed" if changed else "skipped"] += 1
        return stats

    def _next_ids(self, attempted: set[str]) -> list[str]:
        """取下一批待办：先 `pending`，不够再拿「失败但未到上限」。

        ⚠️ **`attempted` 是必需的**：失败但 `fail_count < max_fail` 的篇目在下一批
        `retry_ids` 里**仍然会出现**，不挡掉就会在同一轮里反复重试同一篇 —— 一个
        看上去在推进、实际在死循环的 worker（而且每圈都计费）。
        """
        ids = [i for i in self._vec.take_pending(self._batch_docs) if i not in attempted]
        if len(ids) < self._batch_docs:
            extra = self._vec.retry_ids(self._max_fail, self._batch_docs)
            seen = set(ids)
            ids += [i for i in extra if i not in attempted and i not in seen]
        return ids[: self._batch_docs]

    async def _index_one(self, tx_id: str) -> bool:
        """把一篇文档写进 `vec_chunks`。返回「这次是否真的调用了编码」。

        ⚠️ `search_index_text` 是同步读盘（毫秒级，与启动重建同一路径）。一次
        `to_thread` 换不来什么，反而让「同一篇的读与写不在一个调度单元里」。
        """
        text = search_index_text(self._store, tx_id)
        chunks = chunk_flat(text, self._chunk_size)

        # hash → 旧向量。**按内容匹配**（见模块文档第 2 条）。
        reuse = {c["textHash"]: c["vec"] for c in self._vec.get_chunks(tx_id) if c["vec"]}

        plan: list[dict] = []
        pending_idx: list[int] = []
        for i, c in enumerate(chunks):
            h = chunk_hash(c)
            vec = reuse.get(h)
            plan.append({"idx": i, "text": c, "hash": h, "vec": vec})
            if vec is None:
                pending_idx.append(i)

        if pending_idx:
            vecs = await self._engine.embed([chunks[i] for i in pending_idx])
            # 引擎协议第 1 条契约（`embedding_base`）：返回顺序与入参一一对应。
            # 条数不符说明对面违约 —— 显式抛，别让「文本与向量错位」变成静默的检索降级。
            if len(vecs) != len(pending_idx):
                raise RuntimeError(
                    f"编码返回条数不符：请求 {len(pending_idx)} 条，返回 {len(vecs)} 条")
            for i, v in zip(pending_idx, vecs):
                plan[i]["vec"] = pack_vec(v)

        # 整体替换（原子）→ 块变少时旧行自动消失，不会留下「点进去看不到内容」的孤儿向量
        self._vec.replace_chunks(tx_id, plan)
        self._vec.mark_ok(tx_id, len(plan), content_hash=chunk_hash(text))
        return bool(pending_idx)

    # ── 对账（§4.6 ①②） ──

    def reconcile(self) -> dict:
        """与文档表求差，修掉两种静默不一致。返回 `{orphans, unmarked}`。

        ① **孤儿**（`vec_state` 有、文档表无）→ 删块与状态行。文档被删时
           `vector_store.delete_doc` 应当已被调用，但那条路径可能因为崩溃/旧版本漏掉，
           而残留向量会被检索到 ⇒「点进去文档不存在」。
        ② **漏标**（文档表有、`vec_state` 无）→ 补 `pending`。漏标的来源是历史库
           （向量侧上线之前的文档）与写点异常。

        ⚠️ §4.6 的第 ③ 条（`failed` 且 `fail_count < max_fail` → 重新排队）**刻意不在这里做**：
        它的状态本来就是 `failed`，`run_once` 通过 `retry_ids` 天然会消费它。若在这里把它
        改回 `pending`，`fail_count` 就会被归零（`mark_pending` 的语义）⇒「花了三次钱
        仍然失败」的记录被抹掉，那正是闸门失效的形状。

        ⚠️ 对账**不是重建**：它只补「谁该被索引」这层元数据，一块向量都不重算。
        把重建当日常兜底，等于用高成本掩盖增量漏写。
        """
        out = {"orphans": 0, "unmarked": 0}
        if not self._vec.available:
            return out
        docs = {t.get("id") for t in self._store.list_transcripts() if t.get("id")}
        known = self._vec.known_ids()
        for tx_id in known - docs:
            self._vec.delete_doc(tx_id)
            out["orphans"] += 1
        for tx_id in docs - known:
            self._vec.mark_pending(tx_id)
            out["unmarked"] += 1
        return out

    # ── 常驻循环 ──

    async def loop(self, stop: asyncio.Event, interval: float = DEFAULT_INTERVAL,
                   cooldown: float = RETRY_COOLDOWN) -> None:
        """常驻：对账一次 → 推队列 → 空闲轮询。`stop.set()` 或 task cancel 即退出。

        节奏由「上一轮做了什么」决定，而不是固定节拍：有进展就立刻继续（消化积压），
        队列空就等 `interval`，被闸门 halt 就等 `cooldown`。
        """
        try:
            r = self.reconcile()
            log.info("向量索引对账完成", orphans=r["orphans"], unmarked=r["unmarked"])
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 —— 对账失败不该让 worker 不起
            log.warning("向量索引对账失败（继续跑）", err=f"{type(e).__name__}: {e}"[:200])

        while not stop.is_set():
            try:
                stats = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 —— 单轮异常不能让常驻任务死掉
                log.warning("向量索引 worker 本轮异常", err=f"{type(e).__name__}: {e}"[:200])
                stats = {"indexed": 0, "skipped": 0, "failed": 0, "stopped": "异常"}

            if stats["indexed"] or stats["failed"]:
                log.info("向量索引进度", indexed=stats["indexed"],
                         skipped=stats["skipped"], failed=stats["failed"])

            if stats["stopped"]:
                log.warning("向量索引 worker 暂停", reason=stats["stopped"])
                delay = cooldown
            elif stats["indexed"] or stats["failed"]:
                delay = 0.0
            else:
                delay = interval

            if delay <= 0:
                await asyncio.sleep(0)     # 让出一次控制权，避免长时间独占事件循环
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass                       # 正常到期，继续下一轮

        log.info("向量索引 worker 已停止")
