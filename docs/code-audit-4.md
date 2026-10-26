# 代码质量与缺陷扫描（第四轮）

> **基准**：`dadd8a2` 之上的**未提交工作树**（0.5.x，未升版）。
> 审查对象 = 本轮「语义检索阶段 1–3」落地的全部新增/改动，约 35 个文件：后端 7 个新模块
> （`core/vector_store.py` / `core/vector_index.py` / `services/vector_worker.py` /
> `services/vector_search.py` / `services/vector_runtime.py` / `engines/embedding_base.py` /
> `engines/embedding_dashscope.py`）+ 11 处改动 + 前端 4 个新文件 + 6 个新测试文件。
>
> ⚠️ **与前三轮一样，这是时点快照**，不随代码更新。修了什么、为什么这么修，见本文各条的
> **修复方案**段与 [CHANGELOG](CHANGELOG.md) 对应条目。

## 本轮为什么值得单独扫一遍

前三轮（[1](code-audit.md) / [2](code-audit-2.md) / [3](code-audit-3.md)）扫的分别是既有代码、
既有代码、和一整批新代码。本轮扫的也是新代码，但性质不同：**它给系统加了第二个索引库，
而且这个库的写入是异步的、要花钱的。**

三件事让「自己审自己」在这里格外必要：

1. **一个文档现在有两份索引，而删除、编辑、重建三件写操作都只想到了一份。**
   多出来的那份不会报错，它只是慢慢地和真相不一致 —— 而检索质量下降恰好是「看起来像
   本来就这样」的那类现象。
2. **向量写入是异步的**（写点只打 `pending`，编码归后台 worker）。同步代码里「写完就能读到」
   的直觉在这里全部失效，失效方式是「编辑完文档，语义检索还是旧内容」。
3. **编码要花钱**。任何「不小心重试一次」或「静默重建一次」的设计，用户付出的不是 CPU 时间。

一条贯穿性结论先写在前面，它解释了本轮一半条目：

> **向量索引是「影子状态」（shadow state），而它的维护点分散在四条路径上
> ——写入（`update_index`）、删除（`ingest` ×2）、对账（worker）、重建（`search_vector_rebuild`）。
> 四条路径里**三条做到了，删除那条整条没做**。而缺少的那条**不会报错**，
> 因为对账会在下次启动时把它补上 —— 于是缺陷的可见性被「下次启动」永久推迟了。**

## 扫描方法（可复现）

| 域 | 扫描对象 | 复现手段 |
|---|---|---|
| 存储层 | `vector_store.py` 三表语义 / `vec.db` 落盘 / `mark_pending` 三条写死语义 | 读实现 + 逐条对 `test_vector_store.py` 的 17 条用例；构造 `delete_doc` 后状态断言 |
| 编排层 | `vector_worker.py` 状态机 / `vector_search.py` 融合 / `vector_runtime.py` 开关 | 逐条走失败路径；`reconcile()` 的调用时机对照 `loop()` |
| 引擎层 | `embedding_dashscope.py` 契约（顺位 / 归一化 / 空输入） | 读实现 + 对 `test_embedding_engine.py` 的 11 条 |
| 接入层 | `ingest.py` ×2 删除 / `search.py` 状态与重建 / `core/search.py` 写点 | **全仓 grep `delete_doc` 调用点**（本轮最强的一条证据） |
| 前端 | `search-health.ts` / `SearchIndexBanner` / `SearchIndexCard` / `useDocQuery` | 逐条读状态写入点 + 共享状态单一性 |

⚠️ **不用 Agent 分域扫描**：本轮代码量小（约 2500 行新代码），分域带来的上下文损失
大于并行收益，且上三轮的经验是「Agent 的误报复核成本 ≈ 自己重扫一遍」。全部由我逐文件读。

---

## 条目总览

| slug | 级别 | 一句话 | 状态 |
|---|---|---|---|
| `P1-01-vector-orphans-on-delete` | **P1** | 删文档**从不**清向量 —— 孤块在会话内参与融合、挤占召回名额，要等到下次启动对账才清 | ✅ 已修 |
| `P1-02-status-read-poisons-available` | **P1** | 一次瞬时读失败写进 `_last_error` ⇒ 整个全文检索永久不可用，只有用户手动重建能解除 | ✅ 已修 |
| `P2-01-embedding-key-prefix-in-logs` | P2 | 引擎用标准 logger 打 `key_prefix=api_key[:7]***` → 进内存环形缓冲 → 前端日志面板 | ✅ 已修 |
| `P2-02-embedding-index-set-unchecked` | P2 | 只校验返回**条数**不校验 `index` 集合；重复/越界索引会让文本与向量静默错位 | ✅ 已修 |
| `P2-03-reindex-blocks-event-loop` | P2 | `async def` 里同步调 `engine.rebuild(...)`，千篇库上整机卡几秒到十几秒 | ✅ 已修 |
| `P2-04-semantic-hits-is-truncated` | P2 | `semantic.hits` 实际是 depth 截断后的数（大库恒 ≤50），而注释与文档写「融合前召回篇数」 | ✅ 已修（改措辞） |
| `P2-05-sync-runtime-race` | P2 | `_stop_worker` 在 `await task` 期间已置空引用，并发 `sync` 可起第二个 worker | 🟡 记录不修（窗口极短 + 前端已锁） |
| `P2-06-dead-request-guard` | P2 | `_vector_status` 里两处 `if request else None` 永不触发 | ✅ 已修 |

**修完前五条的成本**：约 40 行改动 + 5 条新用例（另新建 1 个端点测试文件），不新增依赖。
实际落点与逐条对照实验结果见文末「修复记录」（同日）。

---

## P1 — 用户可感知的功能失效（2 项）

### `P1-01-vector-orphans-on-delete` — 删文档从不删向量，「一并清掉」是错的

**是什么**。用户删除一篇文档时，全文索引会被同步清掉，**而向量索引不会**。
`vec.db` 里那篇的 `vec_state` 行与 `vec_chunks` 块原地留下，继续参与检索融合。

**证据**（三条，第一条是决定性的）。

① `delete_doc` 在 `app/` 下的调用点只有一处 —— 对账：

```bash
$ grep -rn "delete_doc" backend/app/
app/core/vector_store.py:436:    def delete_doc(self, tx_id: str) -> None:
app/services/vector_worker.py:192:            self._vec.delete_doc(tx_id)      # ← 唯一的调用者
```

而它的两个语义消费者（`routers/ingest.py` 的单删与批量删）都只调了字面索引：

```python
# ingest.py:129-139（单删）
store.delete_transcript(tx_id)
engine = getattr(request.app.state, "search_engine", None)
...
engine.delete(tx_id)                      # ← 只有字面

# ingest.py:208-211（批量删）
store.delete_transcript(tx_id)
...
engine.delete(tx_id)                      # ← 只有字面
```

② 唯一的兜底 `reconcile()` **只在 `loop()` 开头跑一次**：

```python
# vector_worker.py:209-216
r = self.reconcile()          # 启动时一次
...
while not stop.is_set():      # 之后是纯增量，不再对账
    ...run_once()
```

③ 而这条路径上有两处**用户可见承诺**说它会立刻清：

- [README.md:61](../README.md)：「索引文件存在本机、**删文档时对应向量一并清掉**」
- [SearchIndexCard.tsx:223](../frontend/src/components/settings/SearchIndexCard.tsx)：
  「索引本身存在本机，**删除文档时对应的向量也会一并清掉**」（在开启开关之前，作为隐私承诺）

**为什么重要**。按危害从大到小：

1. **隐私承诺不兑现**。这句话是写在「开启前」的成本与隐私声明里的 —— 用户据此决定要不要
   把正文发给第三方模型。实际是「删了但还留在本机，等下次重启才清」。
2. **会话内污染召回**。孤块会进 `load_matrix`，在 `depth = max(limit, 50)` 的名额里占位。
   库里删得越多，真实结果被顶掉的越多，而**画面上零异常**：
   `_hit_from_store` 按 id 查不到就返回 `None`，那一条被静默跳过（`search.py:84` 的注释还写着
   「孤儿由 worker 的对账清理」—— 判据与实现对不上）。
3. **`vec.db` 只增不减**。长期使用的库，删掉的内容全部堆在磁盘上。

⚠️ **准确描述**：这**不是**永久泄漏。下次启动 `reconcile()` 会清掉
（`known - docs` → `delete_doc`）。缺陷是「**承诺说是立即，实际是下次启动**」，
而桌面应用可以开着好几天不重启。写报告时不夸大成「永不清理」——
这个区别决定了它该不该被当成隐私事故。

**为什么测试没抓到**。[`test_delete_index.py`](../backend/tests/test_delete_index.py) 的三个用例
（`test_delete_via_http_removes_hit` / `test_delete_warns_when_engine_missing` /
`test_delete_warns_when_index_fails`）**全部只测字面索引**；
`test_vector_worker.py` 里有对账清孤儿的用例，但那测的是**补救**，不是「不产生」。
判据覆盖的是「删了之后搜不到」，而向量侧的对应判据从来没人写过。

**修复方案**。两条删除路径各补一句，位置与 `engine.delete` **同处**
（同一把锁、同一段失败显式化）：

```python
vec = getattr(request.app.state, "vector_store", None)
if vec is not None:
    try:
        vec.delete_doc(tx_id)
    except Exception as e:
        log.warning("从向量索引移除失败，语义检索可能仍命中该文档", tx_id=tx_id, error=str(e))
```

⚠️ **必须走 `try` 而不是让它冒成 500**：文档已经删了，回滚不了。
这是与 `engine.delete` 完全同款的取舍（旁边那条注释已经解释过这个理由）。
⚠️ **不要抽成一个「删两处索引」的公共函数**：两条路径的失败措辞、日志字段、
以及「文档已删」这个前提都相同，但字面侧还要区分「engine 缺失」与「delete 抛错」
两种 warning —— 合并会把那两种语义压成一种。

**对照实验**（写进 `test_delete_index.py` 扩展）：

| 步骤 | 当前行为 | 修复后 |
|---|---|---|
| 建一篇 → 等 worker 索引完（`vec.docs.ok == 1`） | 1 | 1 |
| `DELETE /api/transcripts/{id}` | 200 | 200 |
| 立刻查 `vec_store.status().docs.total` | **1（孤块）** | **0** |
| 立刻 `GET /search?q=<该篇独有词>` | 词面搜不到，但若开了语义且该词有邻域向量，可能命中已被跳过的 id | 只返回真实文档 |

**验证状态**：代码路径已 100% 确认（grep 全仓无第三种调用方式）；
「孤块确实进 `load_matrix`」由 `vector_store.load_matrix` 的实现直接推出，**未实机跑过**
（跑一次要开语义开关 + 建索引，属计费路径，本轮按用户「不重要就别实测」的指令跳过）。
修复后应补一条**不花钱**的单测：直接调 `store.delete_transcript` 后断言 `vec.delete_doc` 被调用
（mock `vector_store`）。

---

### `P1-02-status-read-poisons-available` — 读一次状态，可能让搜索永久不可用

**是什么**。`SearchEngine.status()` 自称「只读快照（不抛错）」，但它在异常分支里
**写**了 `self._last_error` —— 而 `available` 的定义就是 `_last_error is None`。
于是一次瞬时的读盘失败，会让整个全文检索从此 503，直到用户手动重建。

**证据**。

```python
# core/search.py:105-107
@property
def available(self) -> bool:
    return self._last_error is None

# core/search.py:123-146（status）
def status(self) -> dict:
    """给设置页 / 诊断用的只读快照（不抛错）。"""
    ...
    if self.available:
        try:
            with self._lock, self._conn() as conn:
                doc_count = conn.execute("SELECT count(*) FROM transcripts_fts").fetchone()[0]
        except Exception as e:
            doc_count = 0
            self._last_error = f"读索引失败：{e}"     # ← 只读查询改写了可用性
```

清空它的只有两处：`rebuild()` 成功（`:267`）与 `_recover_db()`（`:164`）。
中间**没有任何自动恢复**。

对照 `VectorStore.status()` —— 同一套「状态查询」语义，它**只把错误放进返回的 dict**，
不碰实例状态（`vector_store.py`）：

```python
out["lastError"] = ...          # 只进返回值
# self._last_error 一个字节都不动
```

两处同源判据漂移了：同一个词（`lastError`）、两套语义（一个是观察，一个是状态转移）。

**危害链**。

```
GET /api/search/status                    ← 设置页打开 / 文档库横幅挂载 / 任何 GET
  → status() 里 SELECT count(*) 撞上瞬时失败（磁盘忙 / 安全软件占用 / 句柄未释放）
  → self._last_error = "读索引失败：..."
  → available 变 False
  → QueryService.search_resources 抛 QueryError.index_unavailable  (query.py:352)
  → HTTP 503
  → 前端横幅显示「索引不可用」+「重建索引」按钮
  → 用户此刻其实只需要再搜一次
```

⚠️ **不夸大成「跨站攻击」**：`/api/search/status` 确实是**没有来源守卫的 GET**，
`<img src="http://127.0.0.1:7413/api/search/status">` 能触发它；但攻击者**无法主动让它失败**
（要的是 SQL 层异常）。真正的放大器是**调用频率** —— 这个端点被三个地方调
（设置页挂载、文档库横幅挂载、重建后写回），调用越多，碰上一次瞬时失败的机会越大，
而**代价与那一次失败完全不成比例**。

**为什么重要**。这是「判据自己会变」的形态：一个本来用于**观察**的函数，改变了被观察对象的
可用性。项目里已有同款教训（`startup_check` 必须读磁盘而不是读回填后的内存 cfg ——
「判据看的输入不是它以为的那个」，模式 #39）。这里更直接：**判据把观察写进了状态**。

**修复方案**。`status()` 不写 `self._last_error`，异常只落在返回 dict 的独立字段：

```python
def status(self) -> dict:
    ...
    status_error = None
    if self.available:
        try:
            ...
        except Exception as e:
            doc_count = 0
            status_error = f"读索引失败：{e}"     # ← 局部变量，不碰 self
    return {
        "available": self.available,
        "docCount": doc_count,
        "lastError": self._last_error,        # 真·引擎级错误，语义不变
        "statusError": status_error,          # 新增：本次快照自己没读全
        ...
    }
```

⚠️ **不要改成「读到错误就自动 `_recover_db()`」**：那会把一次 `count(*)` 失败升级成删库重建，
而重建要扫全库。观察失败的正确回应是**如实报告这一次没读到**，不是修它。
⚠️ `statusError` 是新字段，前端 `SearchStatus` 类型与 `SearchIndexCard` 要不要展示
可以另议（**默认不展示**——用户对此无法采取行动，多一行红字只是噪音）。

**对照实验**（写进 `test_search.py`）：

```
given 一个正常引擎（available == True）
when  注入 _conn() 抛 sqlite3.OperationalError
      调 status()
then  返回 dict 的 statusError 非空、docCount == 0
      且 **eng.available 仍为 True**          ← 当前实现会变成 False
```

⚠️ **必须断言 `available`，不能只断言 `lastError` 里没这个错**
—— 只断言后者的话，「写进别的字段」和「没写」在测试里看起来一样。

---

## P2 — 一致性与健壮性（6 项）

### `P2-01-embedding-key-prefix-in-logs` — 密钥前 7 位进用户可见日志

**证据**（[embedding_dashscope.py:82-84](../backend/app/engines/embedding_dashscope.py)）：

```python
logger = logging.getLogger(__name__)          # 标准 logger，会 propagate 到 root

logger.info("Embedding 客户端: model=%s base_url=%s key_prefix=%s",
            self.model_id, DASHSCOPE_BASE_URL,
            self.api_key[:7] + "***")
```

而 `main.py:216` 的 `BufferHandler` 挂在 root logger 上，`GET /api/logs` 把它的内容
交给前端 LogPanel（`main.py:409`）。用户贴日志求助时，这行会一起带出去。

**为什么重要**。前 7 位信息量有限（DashScope key 形如 `sk-xxxxxxxx…`，多半只泄 `sk-` +
两三个随机字符），**不是可用的凭据泄漏**。但它与本项目的两条既有纪律直接冲突：

- `mask_sensitive` 对 `GET /api/config` 统一脱敏（连 `***` 都不给类型提示）
- `core/logging.py::_val` 对值截断（记忆 `log-tool-args-privacy`：日志会被用户贴出去）

一处打、一处不打，读代码的人无法判断哪个是规范。

**修复**：直接删掉 `key_prefix` 字段。这一行的价值是「客户端建起来了、model 与 base_url 对不对」，
密钥前缀对排障没有增量信息。若一定要留个标记，用项目既有的脱敏函数而不是手写切片。

**验证状态**：调用链已确认（logger → root → BufferHandler → `/api/logs`）。未实机点开日志面板。

---

### `P2-02-embedding-index-set-unchecked` — 只校验条数，不校验 `index` 集合

**证据**（[embedding_dashscope.py:100-105](../backend/app/engines/embedding_dashscope.py)）：

```python
items = sorted(resp.data, key=lambda d: d.index)
if len(items) != len(batch):
    raise RuntimeError(f"向量化返回条数不符：送 {len(batch)} 条、回 {len(items)} 条")
for i, item in enumerate(items):
    ...                                      # ← 直接用 enumerate 的下标当文本下标
```

而它自己的 docstring 说：「**必须按 `index` 重排**……直接按返回顺序 append 会让
『文本与向量错位』—— 检索质量下降、零报错、单测全绿（假数据通常恰好有序）。这条有专门的测试。」

**但判据只覆盖了一半**：`index` 有**重复**（`[0,0,1]`）或**越界**（`[0,1,3]`）时，
条数照样对得上，`sorted` 之后 `enumerate` 的下标照样 0/1/2 —— 于是

| 送进去的文本 | 拿到的向量 |
|---|---|
| `batch[0]` | `data[0]`（正确） |
| `batch[1]` | `data[0]` **（重复的那条）** |
| `batch[2]` | `data[1]` |

文本与向量错位，而且**完全静默**。这正是它旁边那条注释要防的那件事，只是防漏了一个入口。

**为什么重要**。这是「测试守住了半步」的典型：`test_按_index_重排而不是按返回顺序` 覆盖了
乱序，于是读代码的人以为这个问题解决了。真正的契约是「`index` 是 `[0, n)` 的一个排列」，
而代码只查了 `len`。

**修复**（一行）：

```python
if len(items) != len(batch) or [d.index for d in items] != list(range(len(batch))):
    raise RuntimeError(...)
```

⚠️ 报错措辞要分开：条数不符与索引不符的**处置不同**（前者可能是 `BATCH_SIZE` 与服务端上限
不一致，后者是契约破了/服务端 bug），日志里能区分才有排查价值。

**对照实验**：喂一个 `index=[0,0,1]` 的假 `resp`，当前实现全绿返回 3 个向量；
修复后抛错。**这条不需要真调用 API**，纯 mock 即可，成本为零 —— 建议连同修复一起加。

---

### `P2-03-reindex-blocks-event-loop` — 全量重建在事件循环里同步跑

**证据**（[routers/search.py:130-155](../backend/app/routers/search.py)）：

```python
async def search_reindex(request: Request, engine=..., store=...):
    ...
    engine.rebuild(transcripts, _get_content, vacuum=True)   # 同步：删表 + 逐篇读盘分词 + 写 FTS + VACUUM
```

`rebuild()` 是普通 `def`，内部逐篇 `get_content`（读盘 + 分词）再 `VACUUM`。
千篇级别的库上几秒到十几秒，期间整个事件循环停住 —— 同一进程里的 SSE 令牌推送、
生成进度、对话流全部卡住。

**为什么重要**。项目在这条纪律上已有先例：导出侧 `_build_export_zip` 明确改成了
`await asyncio.to_thread(...)`（注释写了「不再阻塞事件循环」）。同一个代码库里两个长同步操作，
一个包了、一个没包 —— 而没包的这个恰好是用户**主动**点的（他会在点完之后继续用界面）。

**修复**：

```python
await asyncio.to_thread(engine.rebuild, transcripts, _get_content, vacuum=True)
```

⚠️ **`_get_content` 会被带到线程里执行**，它内部走 `search_index_text(store, tx_id)` → 读盘。
`store` 的方法都有 `threading.Lock` 保护（既有设计），安全。但**不要**顺手把 `transcripts`
的获取也搬进去 —— 那是秒级的，没必要。

**验证状态**：读代码确认（`rebuild` 无 `async`）。未实测卡顿时长。

---

### `P2-04-semantic-hits-is-truncated` — 字段名承诺的是「召回数」，给的是「截断后条数」

**证据**。注释（[routers/search.py:48](../backend/app/routers/search.py)）：

> `hits` 是语义路召回篇数（融合前），`0` 且 `error` 为空 = 索引还没有可比向量。

实现（`services/vector_search.py`）：

```python
vector_ids = [ ... ][:depth]        # ← 已经按 depth 截断
semantic["hits"] = len(vector_ids)  # ← 数的是截断后的
```

`depth = max(limit, 50)`，所以开的库一大，`hits` 恒 ≤ 50 而实际召回可能是几百。
前端 `search.ts` 的类型注释抄的是同一句。

**为什么重要**。当前**唯一消费方**（`useDocQuery.ts`）只判 `hits > 0` 与 `=== 0`，
所以**没有实际故障**。问题在于它是个对外契约字段：将来有人据它做「语义覆盖度」显示
（「50 篇相关」）时，会得到一个恒定上限的假数 —— 而那种错误永远查不出来。

**修复**（二选一，推荐前者）：改注释为「语义路进入融合的条数（已按 depth 截断）」，
或返回未截断的召回数（需要把 `[:depth]` 之前的长度留下）。
⚠️ **不要不改也不改文案** —— 这是 CLAUDE.md 里点名过的「文档骗人」形态。

---

### `P2-05-sync-runtime-race` — 开关并发时可能起两个 worker

**证据**（`services/vector_runtime.py::_stop_worker`）的顺序是：
清 `vector_stop` / `vector_task` 引用 → `stop.set()` → `task.cancel()` → `await task`。
`await` 期间让出控制权，此时并发的 `sync_vector_runtime` 看到 `vector_task is None`，
会 `_start_worker()` 建第二个。

**为什么不修**（记录不修）：
- 触发需要两次 `POST /api/search/vector` 在同一事件循环 tick 内并发，前端 `vBusy`
  已经把按钮锁住，正常操作路径到不了；
- 两个 worker 都写同一份 `vec.db`，最坏是重复编码（多花一次钱、结果幂等），
  **不会**损坏数据（`mark_pending` / checkpoint 都是按 `tx_id` 幂等的）；
- 正确修法是给 `sync_vector_runtime` 加一把 `asyncio.Lock`，但那是**新状态**，
  而当前没有任何证据表明这个问题在真实使用中出现过（宁可等它出现，也不要先加锁掩盖它）。

**如果将来要修**：锁加在 `sync_vector_runtime` 入口，不要加在 `_stop_worker` ——
后者被 `main.py` 的关停路径也调用，那里不需要锁。

---

### `P2-06-dead-request-guard` — 两处永远不会走的判空

**证据**（[routers/search.py:117-118](../backend/app/routers/search.py)）：

```python
def _vector_status(request) -> dict:
    vec = getattr(request.app.state, "vector_store", None) if request else None
    state = getattr(request.app.state, "vector_runtime", None) if request else None
```

`request` 由 FastAPI 注入，**必不为 None**。这两处 `if request else None` 是死分支。

**为什么记下来**：不是因为它有害，而是因为它是「防御性代码的假象」—— 读的人会以为
存在「没有 request 的调用路径」，于是不敢删、也不敢依赖这个函数一定要 `request`。
清掉它，函数签名才诚实。

**修复**：删掉两处 `if request else`，签名改成 `def _vector_status(request: Request) -> dict`。

---

## 记录不修（附理由）

以下条目经复核**不构成缺陷**，或**修复成本大于收益**。写在这里是为了让下一轮不必重新论证。

| 项 | 结论与理由 |
|---|---|
| `vector_rank_scored` 未走 `to_thread` | `mat @ qv` 在 655×1024 上约 1ms，且 numpy 运算期间释放 GIL；旁边 `load_matrix` 读盘几十到几百毫秒**已经**包了 `to_thread`。差两个数量级，单独包一层换不来响应性。**本轮曾把它列为候选，复核后撤回。** |
| `_hit_from_store` 逐条查 store 是「N 次磁盘查询」 | **假**。`QueryService.get_resource` 只走内存索引（`store.get_transcript` + `project_resource`），不读盘。50 次是微秒级。**本轮曾列为候选，读到实现后撤回。** |
| `update_index` 的向量分支失败只 warning | 让「编辑保存」因为向量标记失败而报错，比「语义检索里这篇是旧内容」糟得多（前者丢用户的编辑意图）。代价是缺一个可见性出口 —— 但那个出口需要新的状态位（`failed` 不适用，它表示编码失败不是入队失败），收益不抵成本。**已知偏差，不改。** |
| `vec.db` 放在 `{data_dir}`（即 `~/.limo` 旁边） | `vector_store.py` 模块文档已论证体积取舍（「最坏是同步工具多传几百 MB」）。⚠️ **但隐私账没记**：向量是正文的语义派生数据，落进会被同步/备份的目录，而项目为隐私刻意把 DPAPI 密钥库搬出了这个目录。建议**补一句注释**说明这两件事的取舍不同（密钥是「被复制走就可直接使用」，向量是「被复制走需要还原成本」），不是改代码。 |
| `load_matrix` 不缓存 | 模块文档已论证：缓存键（`count + max(rowid)`）会因 SQLite rowid 复用而失效，那比不缓存更危险。 |
| `limo search` 不接语义融合 | [AGENTS.md](../AGENTS.md) 已显式记录为设计决定（`QueryService` 是同步纯读层，两个传输层共用）。 |
| `isIndexUnavailable` 里的字符串兜底 | 应用内不可达（`searchDocs` 必经 `apiFetch`，必抛 `ApiException`），但保留它对外部调用无害，且是「结构化判据在前、字符串兜底在后」的合理层次。 |

---

## 已剔除的假阳性

写这一节是因为**审查的可信度取决于它剔除了什么**，不只取决于它报告了什么。以下每条都曾被
列为候选，复核后判定不成立：

| 候选 | 为什么是假的 |
|---|---|
| `api/search.ts` 三个 POST 没显式带 `X-LiMo-Client` 来源头 | `apiFetch`（[core.ts:73](../frontend/src/api/core.ts)）自动合并 `CLIENT_HEADERS`。所有走 `apiFetch` 的调用都带。 |
| `POST /search/vector` 用 `Depends(get_config)` 会不会拿到磁盘 YAML 直调 `save_config` → 密钥库被无声删除 | 拿到的是 `app.state.config`（内存对象），函数 docstring 明确写明了这条前提。**这条「隐藏前提」本身是正确的**，且有注释保护。 |
| `store.py` 删掉了 `cache_put` / `cache_get` | 全仓 grep 无调用方，是死代码清理，不是回归。 |
| `config.py` 顶层新增 `"search"` 与 `agent_tools` 里的 `search` 键冲突 | 层级不同（顶层 vs 嵌套），读取路径不同，无冲突。 |
| `vector_worker` 的 `MAX_FAIL` 与 `retry_ids` 会不会导致「失败篇永远不重试」 | 不会：`failed` 且 `fail_count < MAX_FAIL` 的篇在 `retry_ids` 里天然会被消费（`reconcile` 的 docstring 专门解释了为什么不在对账里重置 `fail_count`）。设计正确。 |

---

## 测试质量抽检

6 个新测试文件共 82 条用例，命名即断言内容 —— 这是本项目从 [code-audit-3](code-audit-3.md)
P1-04（「脚本式假绿」）之后形成的好习惯。抽检了几条最有价值的：

- `test_按_index_重排而不是按返回顺序` — 直接对应 P2-02 想防的那件事（**但只防了一半**）
- `test_失败_doc_不与同一轮重试` — 失败隔离，正确
- `test_reconcile_does_not_reset_fail_count` — 专门钉住「成本闸门不被日常兜底抹掉」，非常好
- `test_embed_local_只剩转出没有第二份实现` — AST 守卫，钉住「评测与产品共用一份切块」

**缺口恰好落在两条 P1 上**：

| 缺口 | 对应 |
|---|---|
| 没有任何用例断言「删文档时向量被清」 | P1-01 |
| `test_search.py` 只测「正常时 `lastError is None`」，没测「读失败之后仍然可用」 | P1-02 |

判据覆盖的是**成功路径的形状**，而不是**失败之后还剩下什么**。

> ✅ 这两处缺口在同日修复时已补上：`test_delete_index.py` 新增 3 条（单删 / 批量 /
> 向量清理失败），`test_search.py` 新增 1 条（读失败不毒化可用性），另有 2 条钉住
> `index` 集合校验、1 个新文件 `test_search_api.py` 守住两个此前零 HTTP 覆盖的端点。

---

## 修复记录（同日，2026-10-26）

修复顺序按「先两条 P1、再一次性清 P2」执行，每条都配了**能红**的对照实验
（用 `cp` 备份改回缺陷形态跑一遍，恢复后再跑绿）。

| 条目 | 实际落点 | 配套用例 | 对照实验 |
|---|---|---|---|
| P1-01 | `ingest.py` 新增薄委托 `_drop_vectors(request, tx_id)`；单删与批量删两条路径各调一次（与 `_index_for_search` 成对） | `test_delete_index.py::test_delete_drops_vectors` / `test_batch_delete_drops_vectors` / `test_delete_succeeds_when_vector_delete_fails` | 把 `_drop_vectors` 换成 no-op → 前两条红（`实际调用：[]`） |
| P1-02 | `core/search.py::status()` 改用局部 `status_error`，返回 dict 新增 `statusError` 键；`_last_error` 一个字节都不碰 | `test_search.py::test_status_read_failure_does_not_poison_available` | 改回 `self._last_error = ...` → 红 |
| P2-01 | `embedding_dashscope.py` 删掉 `key_prefix` 参数，并写明为什么连片段也不能打 | —（删除本身无行为可测） | — |
| P2-02 | 条数校验之后追加 `indexes != list(range(len(batch)))` 判断，报错措辞与「条数不符」分开 | `test_embedding_engine.py::test_index_集合不符显式报错` / `test_index_越界显式报错` | 条件改成 `if False` → 两条红 |
| P2-03 | `routers/search.py` 改 `await asyncio.to_thread(engine.rebuild, transcripts, _get_content, vacuum=True)` | **新建** `test_search_api.py`（3 条：重建通、来源守卫、向量状态段） | 把 `vacuum=True` 写成位置参数 → `TypeError` → 500 → 红 |
| P2-04 | `vector_search.fuse` 与 `routers/search.py` 的 docstring 改准：`hits` 是「按 `depth` 取、参与融合的篇数」，不是全库命中总数 | 无（纯措辞；行为未变） | — |
| P2-05 | **不动** | — | — |
| P2-06 | `_vector_status(request: Request)` 去掉两处 `if request else None` | `test_search_api.py` 的两条状态用例顺带覆盖 | — |

**为什么 P1-01 抽了 `_drop_vectors` 而 P1-02 没抽公共函数**：前者是**两个调用点、
同一件不可分的事**（取依赖 + 不冒 500），抽出来才不会在下一次新增删除路径时漏掉；
后者只有一处写入点，抽函数只会让「`status()` 是只读的」这条语义多绕一层。

**刻意没做的**：没有把 `status()` 的读失败升级成 `_recover_db()`。那是把「观察失败」
变成「删库重建」——用户点一下设置页就可能丢掉整个索引，代价远大于收益。

**未加显式 UI**：`statusError` 前端类型已加（可选字段），但**刻意不展示**——
用户对它没有可采取的行动（重试即可，下次读就正常了），展示只会让人以为索引坏了。

---

## 已知边界（本轮不验证、不修）

1. **真实库端到端实测未做**（131 篇 / 约 2 元 / 约 3 分钟），按用户 2026-10-26 的指令跳过。
   如实标注：**算法正确性**由 29 题评测与 82 条单测覆盖；**产品链路**（真跑一次开启开关 →
   全库建索引 → 搜索 → 删除 → 再搜索）**未跑过**。P1-01 的危害程度依赖这条链路的真实时序
   （worker 索引完到用户删除之间隔多久），故它的「隐私承诺不兑现」结论是**代码推导**，
   不是实测结论。
2. **`eval_search.py --vector` 的产品侧同轮对照未做**（同一原因）。
3. **本报告的审查部分写于修复之前**（时点快照），随后同日按建议顺序修掉了 7 条，
   逐条落点与对照实验见上方「修复记录」。`P2-05` 按计划保持不动。
   修改后后端全量 `pytest` 为 **1065 passed**。
