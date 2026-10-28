"""重排编排 —— 「同一个候选集，重新定序」的那一层。

与 `services/vector_search.py::VectorSearcher` 并列：那个管「两条路怎么融成一条」，
这个管「融合完的那一条要不要再按相关性排一遍」。两者都**只做编排**，算法住在
`core/vector_index.py`（RRF / 分组排名）与这里（重排后的稳定排序，见 `_apply_scores`）。

## 与「加权 RRF」的分野（这一节的每个数字都来自 `_experiments/05/` 的实测表）

| 手段 | 改的是什么 | 代价 | 实测 |
|---|---|---|---|
| 加权 RRF（`0.8/1.2`） | 两路的**相对可信度** ⇒ 改**召回分布** | 零 | H@1 52.3% → 63.6%，但 H@5 掉 4.5pp |
| 重排（本模块） | **同一候选集内**的先后顺序 ⇒ **不动候选** | 每次一次云端调用（钱 + 延迟） | 待 `_experiments/07/` 测 |

两条判据因此可以互相证伪：
- **重排绝不能改候选集** ⇒ `H@50` 必须与重排前**逐字相同**。少了这条不变式，
  「把一篇本来进不了前 50 的文档排进前 50」会被误记成重排的收益，而那其实是召回的事。
- **重排的增量要在加权之后测**（B vs B+W）。只看「基线 vs 重排」会把
  「排序本来就该调」的那部分算进重排头上，结论会人为指向「必须上模型」。

## 失败一律显式，且**不改变顺序**

超时 / 引擎报错 / 自检不就绪 → 返回**原顺序** + `error` 文案，一次调用都失败就
是「这次没重排」，而不是「搜不到」。`applied=False` 与 `error=None` 并存的含义是
「开了，但候选不足两条」—— 与「压根没开」（调用方拿到 `None`）是两件事。

⚠️ **不重试**：重试把延迟与费用各翻一倍，而失败语义已经明确（用户拿到的是
「这次沿用融合顺序」，不是空结果）。与 `vector_search` 不重试的理由一致。
"""

from __future__ import annotations

import asyncio
import time

from app.core import logging as log

#: 一次重排请求的超时（秒）。三个现存超时的刻度供参照：单条 query embedding 是
#: `3.0`（一条短文本一次往返）、SDK 默认 HTTP 超时是 `300`（批处理级）、
#: 向量索引后台批是 60。这里取 `5.0`：比 embedding 宽松（一次要送 50 段 480 字，
#: 输入明显更长），比任何「用户会以为卡住」的刻度都短。
RERANK_TIMEOUT = 5.0

#: **模型载入**超时（秒），与 `RERANK_TIMEOUT` 刻意分成两个常量（同
#: `vector_search.LOAD_TIMEOUT` 的理由）：管的不是同一件事。
#: 云端引擎没有 `warmup` 钩子 ⇒ 这个值对它们不生效；本地 rerank 模型
#: （bge-reranker 一类）是数百 MB，比 embedding 的 22.8MB 大一个量级，故
#: 不沿用 `LOAD_TIMEOUT = 30.0`。
RERANK_LOAD_TIMEOUT = 60.0

#: 送进重排的候选窗口（条）。**必须 ≥ 最终返回的 `limit`**，否则重排只能在
#: 已经切过的那几条里挑 —— 那时它测出来的收益里混着「候选变少了」的损失。
#: 取 50 的理由：两路融合的 `DEFAULT_DEPTH = 50`，候选池本来就取到这么深；
#: 再往上要付 `max_documents` 的费用，而 50 名之外的文档进 `limit` 的概率很低。
DEFAULT_CANDIDATES = 50


class RerankSearcher:
    """把一个候选列表按「查询 ↔ 文本」的相关性重新定序。

    ⚠️ **构造即代表「开关已开」**（同 `VectorSearcher`）：调用方
    （`services/rerank_runtime.sync_rerank_runtime`）只在 `search.rerank_enabled`
    为真且引擎可用时才建它。本类不读配置 —— 两份开关判据会在「刚关掉、下一次
    请求还没进来」这类时刻分叉。
    """

    def __init__(self, engine, *, timeout: float = RERANK_TIMEOUT,
                 load_timeout: float = RERANK_LOAD_TIMEOUT,
                 candidates: int = DEFAULT_CANDIDATES):
        if engine is None:
            raise ValueError("RerankSearcher 需要一个重排引擎")
        self._engine = engine
        self._timeout = float(timeout)
        self._load_timeout = float(load_timeout)
        self._candidates = _clamp_candidates(candidates)

    @property
    def model(self) -> str:
        """当前模型标识（状态页与设置页读它）。缺 `model_id` 的插件退回引擎名。"""
        return str(self._engine.model_id or self._engine.name)

    @property
    def candidates(self) -> int:
        """候选窗口。**`fused_search` 读它**（`getattr(reranker, "candidates", 0)`）。

        ⚠️ 缺了这个属性，`want` 会退化成 `limit` —— 合并阶段只切 10 条左右，
        重排只能在 10 条里挑。此时**一切正常、零报错**，只是 `_experiments/07/`
        会得出偏低的收益，而看不出偏低的来源是「候选池被压小了」。

        构造时已夹紧（见 `_clamp_candidates`），所以消费方拿到的恒是正整数。
        """
        return self._candidates

    async def rerank(self, query: str,
                     documents: list[str]) -> tuple[list[int], dict]:
        """`documents` → `(新顺序的下标列表, 自证段)`。

        返回的列表长度**恒等于** `len(documents)`：引擎允许只给前 `top_n` 条
        （契约 3），没拿到分数的那些按**原序**追加在尾部 —— 少一个下标就是
        静默丢一篇文档，而结果列表看起来完全正常。

        自证段 `{"error": str | None, "candidates": int, "applied": bool}`：
        `candidates` 是**真正送进引擎**的篇数（受 `max_documents` 与
        `len(documents)` 两者夹紧），它可能小于入参长度。
        """
        n = len(documents)
        report: dict = {"error": None, "candidates": n, "applied": False}
        identity = list(range(n))
        if n < 2:
            # 一条候选重排没有意义（分数都一样，顺序就是它自己）。⚠️ 这里**不调
            # 引擎**：一次注定改变不了任何东西的计费调用。与契约 2「空输入不发起
            # 调用」同一条判据的延伸。
            return identity, report

        reason = self._precheck()
        if reason:
            report["error"] = reason
            return identity, report

        reason = await self._ensure_loaded()
        if reason:
            report["error"] = reason
            return identity, report

        cap = int(getattr(self._engine, "max_documents", 0) or 0)
        docs = documents if cap <= 0 or n <= cap else documents[:cap]
        report["candidates"] = len(docs)

        started = time.perf_counter()
        try:
            ranked = await asyncio.wait_for(
                self._engine.rerank(query, list(docs)), timeout=self._timeout)
        except asyncio.TimeoutError:
            report["error"] = f"重排超时（>{self._timeout:.0f}s），本次沿用融合顺序"
            log.warning("重排超时", timeout=self._timeout, candidates=len(docs))
            return identity, report
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 —— 任何重排失败都只降级这一次查询
            report["error"] = f"重排暂不可用（{type(e).__name__}），本次沿用融合顺序"
            log.warning("重排失败",
                        err=f"{type(e).__name__}: {e}"[:200],
                        candidates=len(docs))
            return identity, report

        order = _apply_scores(ranked, len(docs))
        # 被 `max_documents` 截掉的尾部：按原序追加（它们没被重排过，但一篇都不许丢）
        order.extend(range(len(docs), n))
        report["applied"] = True
        # 只记规模与耗时，**不记 query、不记文档内容** —— 这条 logger 会经内存
        # 环形缓冲进前端日志面板，用户贴日志时顺带外泄的都是隐私。
        log.info("重排完成", candidates=len(docs),
                 elapsed_ms=round((time.perf_counter() - started) * 1000))
        return order, report

    # ── 前置校验 ──

    def _precheck(self) -> str | None:
        """引擎自检（`precheck` 是可选钩子，云端引擎没有 ⇒ 视为就绪）。

        与 `VectorSearcher._precheck` 分层：那个管「索引库 / 口径对不对」，这个管
        「这台机器上引擎能不能起来」。两件事用户该做的动作不同（装依赖 vs 重建），
        合并成一句话会让人无从下手 —— 与 `embedding_local.precheck` 的注释同源。
        """
        check = getattr(self._engine, "precheck", None)
        if check is None:
            return None
        try:
            return check()
        except Exception as e:  # noqa: BLE001 —— 自检自己炸了，结论就是不可用
            return f"重排引擎自检失败（{type(e).__name__}）"

    async def _ensure_loaded(self) -> str | None:
        """确保模型已在内存里（可选钩子 `warmup`，同 `VectorSearcher._ensure_loaded`）。

        ⚠️ 用 `self._load_timeout` 而**不是** `self._timeout`：本地模型读盘要几十秒，
        撞上 5 秒的重排超时会**每次都超时**，而提示写着「重排超时」——读起来像网络慢。
        云端引擎根本没有这个钩子，`getattr` 拿到 `None` 就直接放行。
        """
        warm = getattr(self._engine, "warmup", None)
        if warm is None:
            return None
        try:
            await asyncio.wait_for(warm(), timeout=self._load_timeout)
        except asyncio.TimeoutError:
            log.warning("重排模型载入超时", timeout=self._load_timeout)
            return (f"重排模型载入超时（>{self._load_timeout:.0f}s），"
                    "本次沿用融合顺序")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("重排模型载入失败", err=f"{type(e).__name__}: {e}"[:200])
            return f"重排模型载入失败（{type(e).__name__}），本次沿用融合顺序"
        return None


def _clamp_candidates(value) -> int:
    """候选窗口的**唯一**夹紧点：非数字 / `<= 0` → `DEFAULT_CANDIDATES`。

    ⚠️ 落到默认值而不是 1：`1` 会让重排退化成「只能排一条」，功能在但效果没了 ——
    用户看到的是「开了重排却毫无变化」。脏值（磁盘上手改的字符串、旧版本的
    `null`）该被当成「没设过」，判据同 `vector_threshold` 的读回宽。
    """
    try:
        n = int(value)
    except (TypeError, ValueError):
        return DEFAULT_CANDIDATES
    return n if n > 0 else DEFAULT_CANDIDATES


def _apply_scores(ranked: list[tuple[int, float]], n: int) -> list[int]:
    """`[(下标, 分数)]` → 新的下标顺序（长度 = `n`，前端未覆盖的按原序补在尾部）。

    **排序算子只住这里**（与 `core/vector_index.rrf` 同一条分线：协议只管形状，
    这里只管算法）。三条规则：

    1. **分数大的在前**。
    2. **同分保留输入顺序** —— 用 `sorted(key=-score)` 而不是
       `sorted(reverse=True)`：后者在 CPython 里确实也是稳定的，但语义要读者去
       查文档确认；写成显式负号键，下一个改这里的人不必先证明一件事。
    3. **越界 / 重复下标一律忽略**（引擎契约说会抛，但插件是任意第三方代码）。
       忽略 = 那一条按「没拿到分数」处理，追加在尾部 —— 比把它当作第 0 名强。
    """
    scores: dict[int, float] = {}
    for item in ranked:
        try:
            idx, score = int(item[0]), float(item[1])
        except (TypeError, ValueError, IndexError):
            continue
        if 0 <= idx < n and idx not in scores:
            scores[idx] = score

    order = [i for i in range(n) if i in scores]
    order.sort(key=lambda i: -scores[i])
    order.extend(i for i in range(n) if i not in scores)
    return order
