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

#: **兜底**轮询间隔（秒）。主触发是事件唤醒（`VectorStore.mark_pending` →
#: `loop` 正在等的那两个事件），**毫秒级**；这里只防三件事：唤醒丢失、外部改了
#: `vec.db`、启动瞬间漏标。所以它有多长其实无所谓 —— **它不决定「什么时候建」**。
#:
#: ⚠️ 改造前这里是「唯一触发方式」：写点只打标记不叫醒任何人，于是新导入一篇
#: 最多要等满 60 秒才建索引，而用户完全无从得知（界面上只是「待索引 1」）。
#: 现在「什么时候真的干活」由 `search.vector_trigger` / `vector_threshold` 决定
#: （见 `_should_run`），`interval` 退化成纯粹的心跳。
DEFAULT_INTERVAL = 60.0

#: 触发「连续失败」后的冷却（秒）。比 `DEFAULT_INTERVAL` 长：连续失败几乎总是外部原因
#: （Key 过期 / 网络 / 配额），一分钟内自愈的概率很低，而重试是计费的。
RETRY_COOLDOWN = 300.0

#: 模型内存的释放策略（`search.vector_release`，设置页可改）。**只有占本地内存的引擎
#: 有实际差别** —— 云端引擎没有 `release` 钩子，三档下它一行都不会多跑。
#:
#: - `never`      建完索引后模型一直留着。**每次搜索都是即时响应**，代价是那 ~90MB 常驻。
#: - `idle`（默认）闲置一段时间后释放。日常（低频、天级）等于「建完就放」，而
#:                「刚建完顺便搜一下」那次仍然是热的。
#: - `immediate` **建完索引立刻释放**。内存回到「只有字面检索」的水平，代价是之后
#:                **第一次搜索要等 3–5 秒加载**（这一次由 `VectorSearcher` 显式承担，
#:                不会伪装成「超时」）。
#:
#: ⚠️ **默认 `idle` 不是零回归的妥协，是「默认值 = 改造前行为」这条纪律在此处的正确读法**：
#: 改造前根本没有本地引擎（`vector_release` 是本轮随本地引擎一起加的），云端引擎三档
#: 行为逐字相同 ⇒ 存量用户的观感一个字节都没变。
RELEASE_POLICIES = ("never", "idle", "immediate")
DEFAULT_RELEASE = "idle"

#: **触发方式**（`search.vector_trigger`，设置页可改）。
#:
#: - `auto`（默认）  待编码篇数攒到 `vector_threshold` 时自动建一轮。
#: - `manual`        永不自动建，只有用户点「立即建立索引」才跑。
#:
#: ⚠️ **为什么默认是「攒批」而不是「有活就干」**：本地引擎的模型是 22.8MB 的
#: ONNX，加载要 3–5 秒、常驻约 90MB。为了一篇刚导入的文档反复「加载 → 编码一篇
#: → 闲置释放」是纯浪费，而且 `immediate` / `idle` 两档下它会随每一轮抖动。
#: 攒到一批再一次性处理，加载次数与批次数量成正比而不是与文档篇数成正比。
#:
#: ⚠️ **阈值不是「多久检查一次」**：它与 `DEFAULT_INTERVAL` 是两件事 ——
#: 后者是心跳，前者是闸门。放在一起理解会得出「改成 600 秒也能达到目的」，
#: 那只让单个文档更晚被索引，抖动一点没少。
TRIGGERS = ("auto", "manual")
DEFAULT_TRIGGER = "auto"

#: `auto` 档的篇数阈值（`search.vector_threshold`）。攒够这么多篇待编码的文档，
#: 才值得为它加载一次模型。
#:
#: ⚠️ **达不到阈值 ≠ 建不了索引**：设置页的「立即建立索引」按钮恒在，
#: 它会跳过闸门跑一轮（见 `request_sync`）——「手动」在 `auto` 档下是**兜底**，
#: 在 `manual` 档下是**唯一**通路。少了那个按钮，小库用户会把「没到 100 篇」
#: 读成「这功能坏了」。
DEFAULT_THRESHOLD = 100


class VectorWorker:
    """把 `vec_state` 里的 `pending` 推进到 `ok`。**不持队列、不做超时之外的调度**。"""

    def __init__(self, store, vec, engine, *, chunk_size: int = DEFAULT_CHUNK_SIZE,
                 max_fail: int = MAX_FAIL, batch_docs: int = BATCH_DOCS,
                 release: str = DEFAULT_RELEASE,
                 trigger: str = DEFAULT_TRIGGER, threshold: int = DEFAULT_THRESHOLD):
        if engine is None:
            raise ValueError("VectorWorker 需要一个 embedding 引擎")
        self._store = store
        self._vec = vec
        self._engine = engine
        self._chunk_size = int(chunk_size)
        self._max_fail = int(max_fail)
        self._batch_docs = int(batch_docs)
        #: 模型内存释放策略，见 `RELEASE_POLICIES`。**入口已校验**（路由侧对非法值报
        #: `VECTOR_BAD_RELEASE`），这里不再兜底 —— 静默退化成 `never` 会让「关掉释放
        #: 开关」与「写了错的值」看起来一样，而前者是用户的选择、后者是他的笔误。
        self.release = release
        #: 引擎预热只做一次（见 `_warm`）。失败时不置位 —— 依赖若在此后被装上，
        #: 下一轮（冷却 300s 后）还有机会恢复。**释放模型后会清回 False**：
        #: 下一轮有活时重新预热，而不是让每篇都在 `_index_one` 里各自等一次加载。
        self._warmed = False
        #: `immediate` 档的触发器：**本轮真的建过东西**才置位。没有它，每一轮空转
        #: 都会把用户刚查询时加载的模型踢掉 ⇒ 每次搜索都冷启动。
        self._pending_release = False
        #: 触发方式与阈值，见 `TRIGGERS` / `DEFAULT_THRESHOLD`。**入口已校验**
        #: （路由侧对非法 trigger 报 `VECTOR_BAD_TRIGGER`），这里 clamp 的是**量**
        #: （阈值 ≤ 0 会让闸门永远放行，等于把「攒批」关掉而界面上看不出来）。
        self.trigger = trigger
        self.threshold = max(1, int(threshold))
        #: 「下一轮立刻跑一次，跳过闸门」的旗标（`request_sync` 置位，`loop` 消费即复位）。
        #: ⚠️ **不能直接在这儿 `await run_once()`**：那样会与 `loop` 里的那一轮并发，
        #: 两轮的 `attempted` 集合互不可见 ⇒ 同一篇在两条协程里各编码一次，计费翻倍
        #: 而产物完全正常。所有真正的执行必须留在 `loop` 这一条线上。
        self._force = False
        #: 唤醒事件，在 `loop` 里创建（`asyncio.Event` 只管「有没有人叫过我」）。
        #: `None` = 还没进循环（`request_sync` 在这之前被调用时只置 `_force`）。
        self._wake: asyncio.Event | None = None

    # ── 一轮 ──

    async def run_once(self, force: bool = True) -> dict:
        """把当前队列推到底（或推到失败闸门）。返回 `{indexed, skipped, failed, stopped}`。

        - `indexed` = 真的调了编码的篇数；`skipped` = 块 hash 全中、零调用的篇数
          （空文档也落在这里 —— 它同样「无需编码」，只是块数为 0）
        - `stopped` = 非 None 时表示本轮被闸门中断，值是给用户看的原因；
          空转（下面那条）**不算**被中断，`stopped` 留 `None`

        ⚠️ **`force` 默认 `True`**：这个方法的名字是「跑一轮」，它是**执行层**；
        「什么时候该跑」是**调度层**（`loop`）的事。默认 `False` 会让每个直接调用
        它的脚本/测试都得先想「我的阈值够不够」，而它们要的明明是「现在就推完」。
        ⚠️ 空转是**真的什么都不做** —— 它连 `_warm` 都到不了（`_should_run` 排在
        `_next_ids` 之前），所以「达不到阈值就不加载模型」是结构保证的，不是约定。
        """
        stats = {"indexed": 0, "skipped": 0, "failed": 0, "stopped": None}
        if not self._vec.available:
            stats["stopped"] = "向量索引库不可用"
            return stats

        if not force and not self._should_run():
            return stats

        attempted: set[str] = set()
        consecutive_failures = 0
        while True:
            ids = self._next_ids(attempted)
            if not ids:
                break
            # ⚠️ **预热排在「确认有活」之后**（不是 `run_once` 开头）。队列空着时
            # 加载 22.8MB 权重纯属浪费：`never` 档下它会在每次启动后白占 ~90MB，
            # 而 `immediate` / `idle` 档下更是**每轮空转都加载一次再释放一次**
            # （60 秒一轮，CPU 与内存来回抖）。语义上「第一次 embed 之前预热到」
            # 完全没有变化，只是不再为空转买单。
            if not self._warmed and not await self._warm(stats):
                return stats
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

    # ── 调度 ──

    def _should_run(self) -> bool:
        """这一轮该不该真的干活。**闸门排在编码与预热之前**（见 `run_once`）。

        判据两条，第二优先：

        1. `manual` 档 = 永不自动跑。用户要的是「我说了才建」，那就一次都别自作主张。
        2. `auto` 档 = 待编码数够阈值，**或**有待重试的篇目。

        ⚠️ **重试不受阈值约束**：那批文档已经编码失败过一次（模型为此加载过一回），
        一错一修是纠错不是增长。让它们等攒够 100 篇才重试，等于把可自愈的失败
        拖成「要等一个月」——而 `failed` 在界面上是「失败 N」，用户会以为它坏了。
        数量上限是 `MAX_FAIL` 轮，不会变成长期抖动源。
        """
        if self.trigger == "manual":
            return False
        pending, retry = self._vec.actionable_counts(self._max_fail)
        return retry > 0 or pending >= self.threshold

    def request_sync(self) -> None:
        """请求「下一轮**立刻**跑一次并跳过闸门」（设置页的「立即建立索引」按钮）。

        ⚠️ **只置旗标 + 叫醒循环，绝不自己 `await run_once()`**：那会与 `loop`
        里的那一轮并发，而两轮的 `attempted` 互不可见 ⇒ 同一篇文档被编码两次，
        计费翻倍、产物完全正常、日志里也看不出异常（见 `self._force` 的注释）。
        """
        self._force = True
        self.wake()

    def wake(self) -> None:
        """叫醒循环。**必须在事件循环线程里调用**（`asyncio.Event` 不是线程安全的）。

        跨线程来的那一路（`mark_pending` 常跑在 `asyncio.to_thread` 里）由
        `vector_runtime._start_worker` 注册的回调包了 `call_soon_threadsafe`。
        """
        if self._wake is not None:
            self._wake.set()

    async def _sleep_until(self, stop: asyncio.Event, delay: float) -> None:
        """睡到「超时 / 被叫醒 / 要求停止」三者任一发生。

        ⚠️ **必须同时等 `stop`**：只等 `_wake` 的话，`stop.set()` 之后这个循环
        还要睡满 `delay`（最长 300 秒）才退出。`_stop_worker` 的 `task.cancel()`
        确实能打断它，但那是兜底 —— 而取消会顺带打断正在进行的 `run_once`。
        """
        wake = self._wake
        if wake is None:                       # 理论上进不来（`loop` 开头就建了）
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            return
        tasks = [asyncio.ensure_future(stop.wait()), asyncio.ensure_future(wake.wait())]
        try:
            await asyncio.wait(tasks, timeout=delay, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            # `return_exceptions=True` 把取消产生的 CancelledError 一并收掉，
            # 否则会留下 "Task was destroyed but it is pending" 的日志噪音。
            await asyncio.gather(*tasks, return_exceptions=True)
            # ⚠️ **无条件 clear**：这一轮马上就要跑，留着「已叫醒」的标记会让
            # 下一次空转立刻被消费掉 —— 等于每次唤醒都白跑一轮闸门判断。
            wake.clear()

    async def _warm(self, stats: dict) -> bool:
        """把引擎预热到「第一次编码不用等模型加载」。`False` = 预热失败，本轮放弃。

        ⚠️ **只在有 `warmup` 钩子的引擎上做事**（`getattr` 而非改协议，理由见
        `vector_runtime._engine_precheck`）。云端引擎没有这个钩子，一次调用都不会多。

        ⚠️ **预热失败的后果不是「慢」而是「第一次查询必定超时」**：本地模型加载
        3–5 秒，而 `VectorSearcher.QUERY_TIMEOUT = 3.0` —— 用户开启语义检索后第一次
        搜索会退回纯字面，提示写成「语义检索超时」，读起来像网络问题（而他刚开的
        本地模型根本不联网）。

        ⚠️ **失败绝不进 `fail_count`**：依赖缺失 / 模型文件丢失是「这台机器上跑不了」，
        不是「这一篇编不出来」。记进每篇的失败闸门会让 3 篇之后整库停摆，而真正的
        原因只留在最后三篇的 `error` 字段里 —— 一个把「装个依赖」显示成「3 篇文档
        编码失败」的失败分类错误。
        """
        if self._warmed:
            return True
        warm = getattr(self._engine, "warmup", None)
        if warm is not None:
            try:
                await warm()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 —— 见 docstring 最后一段
                stats["stopped"] = f"向量化模型加载失败：{type(e).__name__}: {e}"
                return False
        self._warmed = True
        return True

    # ── 释放 ──

    def maybe_release(self) -> bool:
        """按 `self.release` 策略决定要不要把模型放掉。返回「这次真的释放了吗」。

        **只在 `loop` 每轮末尾调一次**，不放在 `run_once` 里：单轮 `run_once` 有三种
        退出路径（队列空 / 失败闸门 / 索引库不可用），而「这轮结束了」是 `loop` 才知道的。

        ⚠️ **调用方不传门槛数字**：`idle` 档直接 `release()`，用的是引擎自己的默认
        空闲门槛（`embedding_local.DEFAULT_IDLE_RELEASE`）。门槛「多久算闲置」是
        **引擎的属性**（不同引擎加载代价不同），写进 worker 就是第二真相源 ——
        哪天本地模型换个更小的权重、门槛该变时，改错地方的后果是「闲置判据变了但
        没人发现」。

        ⚠️ **没有 `release` 钩子的引擎一律不做事**（`getattr`，同 `warmup` / `precheck`）：
        云端引擎的「模型」是服务端的事，它连本地内存都不占。
        """
        if self.release == "never":
            return False
        if self.release == "immediate":
            # 只在「刚建完一轮东西」之后放一次。空转轮次一律不动 —— 否则用户搜索时
            # 加载的模型会被下一圈空转踢掉，每次搜索都冷启动（而他刚开的是
            # `immediate`，本来只期望「建库完放掉」这一个代价）。
            if not self._pending_release:
                return False
            self._pending_release = False
            return self._do_release(0.0, reason=f"策略 {self.release}")
        # idle：`after_idle=None` = 交给引擎的默认门槛
        return self._do_release(None, reason=f"策略 {self.release}")

    def release_now(self) -> bool:
        """**不看 `self.release` 策略**，立刻把模型放掉。返回「这次真的释放了吗」。

        唯一的调用方是「语义检索被关掉」这条路径（`vector_runtime._stop_worker`）。
        ⚠️ 刻意绕开策略：`never` 的意思是「**平时**建完索引也留着」，而关掉功能是
        另一个动作 —— 用户不再用这个能力了，**没有「下次搜索」可以等**。用策略挡住
        它就是「选了常驻内存的用户关掉语义检索后，那几十 MB 一直不还」，而界面上
        不会留下任何痕迹。

        ⚠️ 传 `after_idle=0.0` 而不是不传：不传走引擎默认门槛（600s），刚放下键盘
        就关功能时引擎会以「还在闲置期内」为由拒绝 —— 一次什么都不做的释放。
        """
        return self._do_release(0.0, reason="语义检索已关闭")

    def _do_release(self, after_idle: float | None, *, reason: str) -> bool:
        """真正的释放动作（`maybe_release` 与 `release_now` 共用）。

        `after_idle=None` = 不传门槛、由引擎按自己的默认值判闲置。
        """
        release = getattr(self._engine, "release", None)
        if release is None:
            # 云端引擎的「模型」跑在服务端，本机不占内存 —— 三档下它一行都不多跑。
            return False
        try:
            freed = bool(release() if after_idle is None
                         else release(after_idle=after_idle))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 —— 释放失败不影响索引本身
            log.warning("向量模型释放失败", err=f"{type(e).__name__}: {e}"[:200])
            return False
        if freed:
            # 下一轮有活时重新预热。⚠️ 不清回 False 的话，`_warm` 会以为模型还在，
            # 于是每篇都在 `_index_one` 里惰性加载 —— 第一次 embed 白等 3–5 秒，
            # 而 worker 侧没有任何提示。
            self._warmed = False
            log.info(f"向量模型已释放（{reason}）")
        return freed

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
        """常驻：对账一次 → 推队列 → 等「被叫醒 / 心跳到期 / 要求停止」。

        节奏由「上一轮做了什么」决定，而不是固定节拍：有进展就立刻继续（消化积压），
        队列空就睡到下一次唤醒或兜底心跳，被闸门 halt 就等 `cooldown`。

        ⚠️ **主触发是事件唤醒**（`mark_pending` → `wake()`），`interval` 只是兜底心跳。
        但**唤醒不等于干活**：每轮都要过 `_should_run` 闸门，攒不够阈值照样空转 ——
        这正是「模型只在建索引时才加载」的落点（闸门排在 `_warm` 之前）。
        """
        try:
            r = self.reconcile()
            log.info("向量索引对账完成", orphans=r["orphans"], unmarked=r["unmarked"])
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 —— 对账失败不该让 worker 不起
            log.warning("向量索引对账失败（继续跑）", err=f"{type(e).__name__}: {e}"[:200])

        # ⚠️ 必须在**进循环之前**建好：`request_sync` 与写点的唤醒回调都可能在
        # reconcile 期间被调用（那时 `_wake is None`，`wake()` 静默丢弃；但 `_force`
        # 照置位，第一次迭代读得到，不会漏）。
        self._wake = asyncio.Event()

        while not stop.is_set():
            # 旗标**先取后清**：`request_sync` 若在本轮 `run_once` 执行期间到达，
            # 会落到下一轮（那时它该跳过闸门 —— 第一轮的 `_next_ids` 早已取过）。
            force = self._force
            self._force = False
            try:
                stats = await self.run_once(force=force)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 —— 单轮异常不能让常驻任务死掉
                log.warning("向量索引 worker 本轮异常", err=f"{type(e).__name__}: {e}"[:200])
                stats = {"indexed": 0, "skipped": 0, "failed": 0, "stopped": "异常"}

            if stats["indexed"] or stats["failed"]:
                log.info("向量索引进度", indexed=stats["indexed"],
                         skipped=stats["skipped"], failed=stats["failed"])
                # `immediate` 档的触发器：这一轮真的动过队列 ⇒ 允许本轮结束后释放。
                self._pending_release = True

            # 每轮末尾试一次（`never` 档是一次字符串比较，可忽略）。**必须在
            # `run_once` 之外**：那一轮已经推到队列空，此刻才是「建完了」。
            # ⚠️ 放线程池：插件引擎的 `release()` 要抢进程锁，而锁可能被一次编码往返
            # 持有到 180 秒 —— 在事件循环里直接调，等于这段时间整个后端不动。
            # （引擎侧另有 `RELEASE_LOCK_TIMEOUT` 兜底，两层都要。）
            await asyncio.to_thread(self.maybe_release)

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

            await self._sleep_until(stop, delay)

        log.info("向量索引 worker 已停止")
