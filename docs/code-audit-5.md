# 代码质量与缺陷扫描（第五轮）

> **基准**：`76602e9`（0.5.22）之后到 `1a62538` 的工作树，87 个文件、约 7800 行改动。
> 审查对象 = 本轮「导入与转录分开 / 批量导入·转录·生成队列 / 合成合并与简单模式」全部新增与改动。
>
> ⚠️ **这是时点快照，不随代码更新。** 修了什么、为什么这么修，见各条的「修法」段与
> [CHANGELOG](CHANGELOG.md) 2026-11-07 的五条记录。

## 旧四轮的时效性

写这轮之前先说明一件事：仓库里的四份旧报告都已经过期，不能当依据。

| 报告 | 基准 | 到本轮基准 `76602e9` 之间的提交数 |
|---|---|---|
| [code-audit.md](code-audit.md) | `4174166` | 97 |
| [code-audit-2.md](code-audit-2.md) | `6c2ef28`（0.4.16） | 86 |
| [code-audit-3.md](code-audit-3.md) | `cde70b8` 之上的工作树（0.5.0） | 81 |
| [code-audit-4.md](code-audit-4.md) | `dadd8a2`（语义检索阶段 1–3） | 23 |

四份报告的「已知缺口」如果今天还在，它们不会因为报告里没提就消失；反过来，报告里列着的
问题也可能早已修掉。判断某个行为今天成不成立，只能读代码或跑测试，不能读报告。

本轮从 `76602e9` 起算，是因为那之后落地的三块功能改动了导入、转录、生成、合成四条链路
的**状态语义**，而前四轮都没扫过它们。

## 本轮为什么值得单独扫一遍

三块新功能各自引入了一类新的失败形态，且都不在原报告覆盖范围内。

**第一，状态被拆开了，名字还撞在一起。** 「导入与转录分开」之后，库里同时存在
`ready`（store 内部，文档写没写完）、`transcribeState`（用户可见，正文是不是真的）、
`store._pending_ids`（会话级豁免集合）三个都叫 pending 的东西。它们正交，任何一个被
当成另一个用，症状都是「列表和预览区各说各话」，而两边都不报错。

**第二，队列的失败路径比单次调用多一层。** 批量导入、批量转录、批量生成都是串行 `for`
循环，每一项都可能成功、失败、被用户取消，循环本身还可能因为意料之外的抛错退出。
单次调用只有「成功 / 失败」两条出口，队列有四条，而收尾语句只写在最顺利的那条上就会失效。

**第三，参数方向开始不一致。** 两个档位参数的默认值相反（`media_probe_only` 默认
`True`、`doc_probe_only` 默认 `False`），「只在偏离默认时才发」这条纪律在两个参数上
要求的方向就相反。写成同一条对称判断，其中一个方向必然永远发不出去。

一条贯穿性结论：

> **本轮几乎所有可感知缺陷都出在「某个状态有两处表示，而只有一处被更新」。
> 列表与预览区、前端与后端、设置页与请求参数，三对都是同一个形状。**

## 扫描方法（可复现）

四路分域并行，每路给同一个 diff 范围与同一套口径：只报能指出具体文件行号、说明用户
可见症状、并给出触发路径的项；只有结论没有触发路径的一律不收。

| 域 | 扫描对象 | 判定口径 |
|---|---|---|
| 后端质量 | `doc_text` / `core/search.py` / `generate.py` / `synthesis.py` / `ingest.py` 的状态读写 | 每个状态字段的写入点是否覆盖全部读取点 |
| 前端文档库与批量 | `useBatchImport` / `useBatchTranscribe` / `useBatchGenerate` / `BatchBar` / `DocRow` | 队列的四条出口各走到哪句收尾；取消与失败是否被区分 |
| 前端导入与契约 | `api/ingest.ts` / `ingest-prefs.ts` / `GenPanel` / `useStartTranscription` | 前后端参数名与默认值是否逐项对齐 |
| 安全 | 本轮 diff 的 87 个文件 | 只报 HIGH / MEDIUM，且要求具体触发路径 |

安全面结论：**无 HIGH / MEDIUM 发现**。这一点与其后列的「记录但不修」一起说明。

四路报告的原始候选合计十余条，复核后成立并进入本报告的只有 5 条。其余候选的处理分两类——
能说明「为什么不构成问题」的写进「记录但不修」，被证据推翻的写进「已剔除的假阳性」。

---

## 条目总览

| slug | 级别 | 一句话 | 对应 CHANGELOG | 状态 |
|---|---|---|---|---|
| `P1-01-auto-transcribe-switch-dead` | **P1** | 「导入视频后自动转录」打开与关闭行为完全相同，参数被前端吞掉 | 审查第 1 条 | ✅ 已修 |
| `P1-02-pending-flow-into-downstream` | **P1** | 占位正文流进全文索引、向量编码（计费）、生成端点 | 审查第 3 条 | ✅ 已修 |
| `P1-03-backfill-blocked-by-navigation` | **P1** | 转录期间切走去看别的文档，列表徽章永远不消失 | 审查第 4 条 | ✅ 已修 |
| `P1-04-queue-teardown-skipped` | **P1** | 循环体抛错时队列收尾一行不跑，对话框永久停在「运行中」 | 审查第 5 条 | ✅ 已修 |
| `P2-01-import-abort-controller-leak` | P2 | 导入任务的 `AbortController` 结算后从不摘除 | 审查第 2 条 | ✅ 已修 |
| `R-01-synthesis-no-origin-guard` | — | 三个合成端点没有来源守卫 | — | 🟡 记录不修（不构成问题） |

**修复成本**：5 个主题、5 次提交（`2e02d3a` / `481f2a7` / `d0fb3d9` / `f4a2892` / `dcd2000`），
20 个文件、988 行新增、155 行删除（其中 160 行是 CHANGELOG）。新增两个测试文件
（`test_pending_guard.py` / `transcribeBadge.test.tsx`），五个既有测试文件各补一组用例。
不新增依赖。

---

## P1 — 用户可感知的功能失效

### `P1-01-auto-transcribe-switch-dead` — 开关关不掉，设置页承诺不兑现

**是什么。** 设置页有「导入视频后自动转录」与「导入文档、网页后自动提取正文」两个开关。
第一个开关**无论打开还是关闭，视频都只收纳**。

**证据。** 两个档位参数的后端默认值相反：

```
media_probe_only: bool = Form(True)    # 视频只收纳
doc_probe_only:   bool = Form(False)   # 文档网页一键提取
```

按「只在偏离默认时才发」的纪律，媒体档要发的是 `false`（用户打开了自动转录），文档档要发
的是 `true`（用户关掉了自动提取）。而 `api/ingest.ts` 对上两者写了同一条判断：

```ts
if (mediaProbeOnly) body.set('media_probe_only', 'true')
```

用户打开自动转录时 `mediaProbeOnly` 是 `false`，正是该发出去的那一刻，被这个条件吞掉。
后端读不到参数，用默认 `True`，于是开关的两个位置映射到同一个请求。

**用户可见症状。** 设置页显示已打开，导入视频后列表仍挂「待转录」徽章，需要手动点一次
「开始转录」。没有任何一处报错，用户会怀疑是设置没保存。

**触发面。** 所有在设置页打开过该开关的用户，全部视频导入路径（`/ingest` 与
`/ingest/stream` 共用同一个 body）。

**修法。** 两个方向分开写，判据取「要发的值」而不是「参数名」：

```ts
if (opts?.mediaProbeOnly === false) body.set('media_probe_only', 'false')
if (opts?.docProbeOnly === true) body.set('doc_probe_only', 'true')
```

**测试与对照实验。** `ingestPrefs.test.ts` 的「请求参数」组共 5 条，用假 `fetch` 直接断言
body 里出现或不出现哪个键。对照实验：把条件改回真值判据，其中 3 条立刻变红，另外 2 条
（省略默认值、文档档偏离默认）仍绿——后者两处旧写法恰好也发对了，说明只测一个方向抓不到
这个缺陷。

---

### `P1-02-pending-flow-into-downstream` — 占位正文被当真正文处理

**是什么。** 收纳模式下写进磁盘的正文是一段占位：

```markdown
<!-- limo-pending:{tx_id} -->
> 这篇文档已收纳，正文还没有转录。
> 在预览区点「开始转录」即可提取正文。
```

它是一段合法 Markdown、非空。任何按「正文非空」判断的下游都会放行它，于是出现三种后果。

| 下游 | 后果 |
|---|---|
| 全文索引 | 「这篇文档已收纳」「点开始转录」成为可搜噪音，每条待转录文档都命中 |
| 向量 worker | 给占位文本编一次码；云端引擎下**每篇一次计费调用** |
| 生成端点 | 拿占位文本生成一篇结构完整的笔记，通篇在讲「这篇还没有转录」 |

前两条是噪音与费用，第三条最严重：产物看起来是正常的笔记，用户很难意识到生成输入
只有两句话。

**触发面。** 所有待转录文档。向量那条尤其隐蔽——用户看不到 worker 在做什么，只在账单上
看见。

**修法。** 判据只加一处，`generation/doc_text.py::transcript_pending`：

```python
return (tx or {}).get("transcribeState") == "pending"
```

`== "pending"` 而不是 `!= "done"`：缺键是历史文档，读作已转录。反过来写会把全部历史文档
判成待转录，它们会突然搜不到正文、也无法生成笔记，而库里一个字节都没变。

三个消费方共用这一处判据：

- `search_index_text` 返回空串（**不是**跳过整条索引）。`SearchEngine.index` 照常写标题列，
  于是「记得标题就搜得到」仍然成立——用户收纳完过几天回来找的正是标题；
- `core/search.py::update_index` 不给待转录篇打向量 `pending`。读不到状态也跳过，宁可少标
  一次，也不要给一个可能没有正文的篇目排一次计费编码；
- `routers/generate.py::_ensure_transcribed`，两个生成入口（`/generate/{id}` 与
  `/generate/{id}/stream`）共用一份。前端在待转录时禁用按钮，但批量队列、外部脚本、
  将来的其它入口都直接打端点，前端拦过不等于接口层不用拦。

新错误码 `GEN_NOT_TRANSCRIBED`，`hint` 点名「在预览区点『开始转录』，或在文档库里选中它
批量转录」。位置在两个入口的 `GEN_NOT_FOUND` 之后：先判存不存在，再判转录没转录。

**测试与对照实验。** `test_pending_guard.py` 7 条，四条对照实验各自摘掉一处修复验证必红：
把判据改成 `!= "done"`、`search_index_text` 去掉分支、`update_index` 去掉跳过、
`_ensure_transcribed` 不再抛错。

---

### `P1-03-backfill-blocked-by-navigation` — 转录回填受「用户还看着这篇吗」约束

**是什么。** 转录成功后要做两件事：改预览区（`currentRawText`）与改 `docs` 列表里那条的
`transcribeState`。这两件事被 `applyTranscriptionResult` 写在一起，挂在 `useTaskRunner`
的 `onSuccess` 上，而 `onSuccess` 前面有一句导航检查：

```ts
const result = await run(signal)
if (!isSame()) return          // 用户切走了？整个成功副作用都不跑
await opts.onSuccess?.(result)
```

**用户可见症状。** 转录动辄几分钟，期间切去看别的文档是常态。在这种最常见的路径上，
转录完成后列表徽章一直挂着「待转录」，直到用户切去文档库重新拉一次列表。预览区里
正文其实是新的，两处信息公开矛盾。

**触发面。** 所有「转录期间切走」的场景。预览区待转录卡片与 GenPanel 的重新转录按钮
两条入口都受影响。

**修法。** `useTaskRunner` 加 `onSuccessAlways`，在导航检查之前调用：

```ts
const result = await run(signal)
await opts.onSuccessAlways?.(result)   // 与导航无关的副作用
if (!isSame()) return
await opts.onSuccess?.(result)         // 只在仍看着这篇时执行
```

两个回调的分工判据是「这件事在切走之后还成不成立」，不是「代码好不好拆」。
`useStartTranscription` 把 `applyTranscriptionResult` 挪到 `onSuccessAlways`，
`onProducts` 与 toast 留在 `onSuccess`——产物重载动的是预览区，切走了就不该跑。

`applyTranscriptionResult` 内部仍判 `currentDocId`，那属于「该不该改预览区」，
与「该不该改列表」是两件事。批量转录本来就走自己的队列，共用的是这同一个函数。

**顺带修的同主题缺陷。** 「待转录」徽章此前只加在简略（26px 单行）分支里，而详细是默认
密度，多数用户看不到它。详细分支补一个同判据、同配色的标签。两处判据都写 `=== 'pending'`：
缺键是历史文档，写成 `!== 'done'` 会让升级后全部历史文档突然挂上徽章。

**测试与对照实验。** `startTranscription.test.tsx` 新增「转录期间用户切走了」一组，
`transcribeBadge.test.tsx` 4 条（两种密度 + 已转录 + 缺键）。对照实验：把
`applyTranscriptionResult` 挪回 `onSuccess`，前者红；删掉详细分支的标签，后者红。

---

### `P1-04-queue-teardown-skipped` — 循环抛错时队列收尾一行不跑

**是什么。** `useBatchTranscribe` 与 `useBatchGenerate` 的收尾语句——`setPhase('done')`、
`setBackground(false)`、`clearQueue(...)`、`markQueueIdle(...)`、汇总 toast——写在 `for`
循环**后面**。

**用户可见症状。** 循环体里只要有一处意料之外的抛错，这些语句一行都不会跑：

- 对话框停留在「运行中」，出不来结果；
- `mbw_batchQueues` 里的快照还写着「在跑」；
- 恢复入口不会再出现（它按「本页是否在跑」判断）；
- 界面上没有任何报错。

用户关掉对话框再重新打开文档库，看到一条指向那批文档的恢复横幅，点进去又回到同样的状态。

**触发面。** 任何循环体里的未捕获异常。「意料之外」在这里是字面意思：已知的失败点
（单篇请求失败、取消）早已各自处理，这一条管的是剩下的。

**修法。** 两层防护，分工明确：

1. 外层：整段循环包进 `try`，收尾挪进 `finally`。走到 `finally` 时仍是 `queued` 的项标成
   `skipped` / `'未执行'`——对话框已经在显示结果了，不能让它们永远停在排队态。
2. 内层：回填与刷新各包一层 `try/catch`，失败仍算这一篇完成。转录成功后要
   `getTranscript` 回填正文、生成成功后要 `getMarkdown` 刷新产物；这一步失败时后端
   **已经写盘了**，报成 `failed` 会把用户引去重跑一次完整任务（几分钟 + 可能计费），
   而正文或笔记就在那儿。所以状态仍是 `done`，把没做成的那一步写进 `message`
   （「已转录，但刷新正文失败：…」）。

两层的关系：内层兜已知的触发点，外层兜未知的。只做外层，回填失败会被记成这一篇失败，
用户得到的是错误的下一步动作；只做内层，其它意外仍然卡住队列。

**测试与对照实验。** 两个批次测试文件各加「回填失败不算失败」与「循环里抛错时收尾照样
执行」，共 4 条。第二条用例把 `setProcessing` 临时换成抛错的版本，制造循环体内未捕获的
异常。对照实验：把两个 hook 回退到修复前，这 4 条全红、其余 15 条全绿。

---

## P2 — 无用户可见症状，但会持续累积

### `P2-01-import-abort-controller-leak` — 导入的 AbortController 从不摘除

**是什么。** 导入任务在 `taskManager` 里的 key 是 `__import__{自增序号}__import`，序号只增
不减。注册进去的 `AbortController` 在任务结算后从不摘除，每导入一次就在模块级 Map 里留下
一条永远不会再被查询的条目。

**为什么文档任务没有这个问题。** 它们的 key 归一化到 `docId`，下一次任务会复用同一个 key，
等于覆盖写。导入的 key 每次都新，覆盖写不成立。

**影响面。** 只有内存，随会话时长线性增长。界面无异常，没有报错。

**修法。** `api/taskManager.ts` 加 `unregisterAbort(taskKey)`。调用点必须与结算同处——
提前调会让「取消」按钮点不动（找不到 controller）：

- 批量导入：落在 `runOne` 的 `finally`，三种结局（`done` / `error` / `aborted`）都经过它，
  且此刻 controller 已经不会再被 abort；
- 单篇导入：没有统一的 `finally`，落在两个入口各自的 `onDone` / `onError` 四处。

**测试。** `taskManager.test.ts` 用 `cancelTask` 的布尔返回值当观测点：释放后返回 `false`
且 signal 没有被 abort；释放一条不影响并发的另一条；释放未注册的 key 幂等。

---

## 记录但不修

### `R-01-synthesis-no-origin-guard` — 三个合成端点没有来源守卫

`POST /api/synthesis/stream`、`/preview`、`/merge` 都没有挂 `core/origin.py::ensure_trusted_request`，
而 `chat.py`（3 处）、`generate.py`（2 处）、`plugins.py`（7 处）、`search.py`（7 处）都挂了。
按「新增任何写端点先看这里」的纪律，这看起来是漏的。

**判定：不构成问题，不修。** 守卫的必要性来自「有副作用的 GET」这一类请求——跨站
`<img>` / `<script>` 是 no-cors、带不了自定义头，所以 GET 会被发出去而读不到响应。
三个合成端点都是 `application/json` 的 POST：跨站表单发不出这个 content-type，要发就得走
预检，而 CORS 白名单会拒掉预检。对比 `generate.py` 里挂守卫的那个端点，它确实是有副作用的
GET，那是必须挂的形态。

**代价与边界。** 将来若给合成加 GET 形态的入口（例如把预览改成 GET 带查询参数），
这条判定立刻失效，那时必须同批挂守卫。写在这里正是为了那一刻能被找到。

## 已剔除的假阳性

**安全面无 HIGH / MEDIUM。** 安全域扫了本轮 87 个文件，重点覆盖导入与转录链路、批量队列、
主题与 localStorage 读写、占位正文的渲染路径。确认的事实：

- 全仓 `.tsx` 没有 `dangerouslySetInnerHTML` / `innerHTML` / `eval` / `new Function` /
  `document.write`；
- `PendingTranscript` 只渲染固定文案与按钮，占位正文里的 `<!-- limo-pending:… -->`
  **不进** `MarkdownPreview`（它抢在 `case 'transcript'` 之前分叉）；
- 导出 HTML 自带 CSP nonce，正文里的 `<script>` 与 `on*` 不执行；
- 资产搬运与文件服务路径都有 `relative_to` 守卫。

**「`markArtifact` 排在导航判断之后」不成立。** 审查候选里有一条说
`useBatchGenerate.refreshArtifacts` 的 `markArtifact` 在 `currentDocId` 比较之后，于是
「生成完切走」会漏刷新 `docs` 里的 `modes`。读代码确认顺序相反：`markArtifact` 在
`currentDocId !== id` 的 `continue` 之前，与该函数 docstring 的描述一致。这条是照 diff
片段推断产生的误报，且第 5 条修复引入的测试用例正好以「A 的 `modes` 已含 `notes`」为断言，
有区分力。

**其余候选未进入本报告。** 有些候选只能给出「这里看起来不对」而没有具体触发路径
（例如单看某个函数缺少兜底），按本轮口径不收。不收不等于确认它们是安全的，只是这一轮
没有证据支持。

---

## 对照实验

每一条都要求「回退修复 → 对应断言必须变红」，用来证明判据有区分力，而不是恰好绿着。

| 回退哪一处 | 必须变红的用例 |
|---|---|
| `ingest.ts` 的条件改回真值判据 | `ingestPrefs.test.ts` 第 4 组 4 条 |
| 去掉批量导入 `runOne` 的 `finally`、四处 `onDone`/`onError` 的释放 | `taskManager.test.ts` 3 条 |
| `transcript_pending` 改成 `!= "done"` | `test_pending_guard.py` 历史文档那条 |
| `search_index_text` 去掉待转录分支 | `test_pending_guard.py` 索引正文那条 |
| `update_index` 去掉跳过 | `test_pending_guard.py` 向量标记那条 |
| `_ensure_transcribed` 不再抛错 | `test_pending_guard.py` 生成闸门那条 |
| `applyTranscriptionResult` 挪回 `onSuccess` | `startTranscription.test.tsx` 「转录期间切走了」 |
| 删掉详细密度的徽章标签 | `transcribeBadge.test.tsx` 详细密度那条 |
| 两个批量 hook 回退到修复前 | 4 条新用例全红，其余 15 条全绿 |

## 门禁

- 后端：`pytest tests/test_pending_guard.py tests/test_ingest_pending.py tests/test_store_pending.py`
  —— 29 条通过（1.81s）。
- 前端：本轮修动的六个测试文件
  （`ingestPrefs` / `taskManager` / `startTranscription` / `transcribeBadge` /
  `batchGenerate` / `batchTranscribe`）—— 48 条通过（2.23s）。
- 提交：五个提交各跑过一次 pre-commit 的 `quick`（tsc + oxlint）与 `docs`（文档节号交叉引用）。
