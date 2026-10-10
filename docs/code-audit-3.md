# 代码质量与缺陷扫描（第三轮）

> **基准**：`cde70b8` 之上的**未提交工作树**（0.5.0，未升版）。
> 审查对象 = 本轮「产物版本管理重做」（两版制 → 版本归档）落地的全部改动，41 个文件。
>
> ⚠️ **与前两轮一样，这是时点快照**，不随代码更新。修了什么、为什么这么修，见本文各条的
> **修复方案**段与 [CHANGELOG](CHANGELOG.md) 2026-10-10 条目。

## 本轮为什么值得单独扫一遍

前两轮审计（[code-audit.md](code-audit.md) / [code-audit-2.md](code-audit-2.md)）扫的是**既有代码**。
本轮扫的是**刚写完的新代码** —— 而这一批改动的性质让「自己审自己」变得必要：

**写入语义从「新建」变成「就地覆盖」**。旧模型下写错了顶多是「多一个版本」；新模型下
`replace_version=N` 写错，**直接覆盖掉用户没在看的那个版本，原文不可恢复**。同一份代码，
同样一个笔误，危害面完全不同 —— 这不是「再检查一遍」，是**语义升级带来的新风险面**。

三个并行 Agent 分域扫描（存储层 / 路由与导出层 / 前端状态层），每条发现由我独立复现。
**Agent 报告中经复核不成立的条目已在过程中剔除，本文只收录能复现的。**

## 扫描方法（可复现）

| 域 | 扫描对象 | 复现手段 |
|---|---|---|
| 存储层 | `store.py` 新布局 / 迁移 / 重建 / `save_markdown` | 造临时库跑迁移与重建，比对前后 index；注入旧实现做对照实验 |
| 路由与导出层 | `generate.py` / `export.py` / `markdown_edit.py` / `query.py` | 三处 `version` 判据逐值对表；`chars` 缺失路径构造 |
| 前端状态层 | `PreviewArea.tsx` 的 `markdownMeta` 生命周期 / `VersionSwitcher` | 逐条读状态写入点；三条源码守卫对照实验 |

一条**贯穿性结论**先写在前面，它是本轮一半条目的共同成因：

> **`markdownMeta` 是「用户此刻在看哪一版」的载体，而它在本轮之前只为一个 mode 设计过。**
> notes 与 polished 是两个独立产物位、版本号各自从 v1 起，而 `markdownMeta` 是**单值** ——
> 旧模型下这只导致「版本下拉显示了别的 mode 的列表」；新模型下它直接决定**编辑往哪个文件写**。

## 条目总览

| slug | 级别 | 一句话 | 状态 |
|---|---|---|---|
| `P0-01-migration-rebuild-wipes-index` | **P0** | 启动迁移末尾调 `_rebuild_index(save=True)` → 全库标签/文件夹/来源/时间被清空并落盘 | ✅ 已修 |
| `P1-01-preview-meta-cross-mode-lag` | **P1** | 跨 mode 切 tab 后 `markdownMeta` 滞后 → 编辑就地覆盖**用户没在看的那一版** | ✅ 已修 |
| `P1-02-artifact-version-chars-fake-zero` | **P1** | 版本条目缺 `chars` 时填 `0` —— 「不知道」被写成「没有」（伪造数据） | ✅ 已修 |
| `P1-03-loadversion-stale-response` | **P1** | 版本切换归属守卫只比 docId，不比 version → 连点 v1/v2 会互相覆盖 | ✅ 已修 |
| `P1-04-export-test-silent-green` | **P1** | 新导出测试是脚本式：`pytest` 收集 0 条（假绿）+ 自带 handler 与生产分叉 | ✅ 已修 |
| `P2-01-negative-version-divergence` | P2 | `version` 判据三处写法不一（`> 0` vs 非 0），负数被静默当最新 | ✅ 已修 |
| `P2-02-save-markdown-mutates-arg` | P2 | `save_markdown` 就地改写调用方传入的 `file_meta` 字典 | ✅ 已修 |
| `P2-03-to-markdown-meta-not-converged` | P2 | `MarkdownView → MarkdownMeta` 映射的收口函数**没被用上**，两处仍是内联字面量 | ✅ 已修 |
| `P2-04-private-domain-suffix-gap` | P2 | 代理豁免表缺保留私有域名后缀，`.local` / `.internal` 网关被塞进代理 | ✅ 已修 |
| `P2-05-markdown-view-type-duplication` | P2 | `MarkdownVersion` 在 `api/generate.ts` 与 `store/types.ts` 各声明一份 | 🟡 记录不修 |

---

## P0 — 用户可直接触发（1 项）

### `P0-01-migration-rebuild-wipes-index` — 一次启动迁移，抹掉全库文档级字段

**证据**（[store.py](../backend/app/storage/store.py) `_migrate_legacy_markdown_layout`）

迁移函数写完时做了一件**看起来最省事**的事：搬完文件后调一次
`self._rebuild_index(save=True)` 把索引刷新到位。而 `_rebuild_index` 的第一行是
`self._index = _empty_index()` —— 它从**磁盘**重扫。

磁盘上有什么：`transcript.md`（正文）、`transcript/index.json`（每个文档自己的元数据）、
各 mode 的产物文件。磁盘上**没有**什么：`index.json` 里的 `tags` / `folderId` /
`sourceUrl` / `createdAt` / `id`，以及 `folders` 整张表。这些**只存在于 `index.json`** ——
`_rebuild_index` 扫不出来，只能给默认值（空串 / 空列表 / 重推的 id）。

而 `save=True` 意味着**扫完立刻 `_save_index()` 落盘**。于是：

```
升级用户首次启动 → 迁移搬文件 → 重建索引（字段清空）→ 立刻写盘 → 不可逆
```

**用户可见症状**

- 升级后所有**标签**消失、所有**文件夹归属**消失（文件夹列表本身也没了）。
- 所有文档的**导入时间**变成空 / 重建时刻，**来源 URL** 丢失 —— 去重（`find_by_url`）随之失效。
- `folders` 表被清空，但 `index.json` 里文档的 `folderId` 也已经没了 → **无法恢复**。
- **零报错**。日志里只有一句「产物布局已迁移：N 个文件…」，读起来像成功。

**触发面**：**每个老用户的第一次启动都会触发**。判据是磁盘上有没有两版制产物（`{mode}.md`
/ `{mode}.prev.md`），而只要该用户生成过一次笔记，就必然有。不是边界情况。

**为什么差点漏掉**：写这段时的注意力全在「文件有没有搬对」上，而 `_rebuild_index(save=True)`
在别处读起来就是「刷新一下索引」。**它的名字没有说谎，是调用位置错了** —— 重建是「索引坏了
时的自愈手段」，不是「写完文件后的收尾动作」。

**修复方案**

1. 迁移末尾**不重建**，改为只重算**受影响的 `(文档, mode)`** 那几个产物条目（`_scan_markdown_versions`
   单 mode 扫描），其余条目原样保留 —— 包括 index 独有的 `status="partial"` 等字段。
2. 新增 `_markdown_entries_desynced(tx_id, doc_dir, mode)`：判据是**版本集合比较**
   （磁盘 vs index），而**不是**「本次搬了几个文件」。于是它同时覆盖三种状态 —— 正常迁移
   （旧条目号与文件名对不上）、迁移崩在半途（文件已改名、index 未更新）、index 被外部改坏
   —— 三者收敛到同一条修法。在**已迁移且一致**的库上恒为 `False`，这是「正常启动零副作用」的保证。
3. 写盘失败**不阻断启动**：`_save_index → _atomic_write` 抛的是 `StoreError`（`AppError` 子类，
   **不是 `OSError`**），只 catch `OSError` 会让磁盘满 / 文件被占用时后端起不来。内存结果不落盘
   是安全的 —— 下次启动 `_markdown_entries_desynced` 会再判一次（集合比较天然幂等）。
4. 新增 3 条回归测试（见「对照实验」），并让**原有 6 条全绿作为对照**。

**危险度判据**：破坏性 × 触发面 ÷ 可察觉度。这是本轮唯一一个**每个老用户首启必现、不可逆、
且日志说成功**的缺陷 —— 三项都拉满。它也是「审计确实有必要」的实证：这份代码通过了当时
已有的全部 6 条迁移测试。

---

## P1 — 静默失效 / 数据丢失路径（4 项）

### `P1-01-preview-meta-cross-mode-lag` — 编辑会覆盖「用户没在看的那一版」

**证据**（[PreviewArea.tsx](../frontend/src/components/preview/PreviewArea.tsx)）

`markdownMeta`（含 `mode` + `version`）是**单值**，而 notes 与 polished 是两个独立产物位、
**版本号各自从 v1 起**。现状是：

| 位置 | 改动前的行为 |
|---|---|
| 加载文档 | 只写 notes 的 meta（`md` 取 notes 优先） |
| 切到 polished tab | **什么都不做** —— `markdownMeta` 仍是 notes 的 |
| `startEditing` | `const version = markdownMeta?.version ?? 0` → 拿 notes 的版号去改 polished |

于是链路是：**切到整理版 → 编辑 → 保存 → 后端按 `replace_version=N` 覆盖整理版的第 N 版**
（而 N 是笔记的版号）。就地覆盖语义下，**用户正在看的那一版没被改，另一个版本被改了**。

`0` 这个兜底还要单独说：它在旧模型（编辑 = 新建版本）里是「最新版」这个无害默认值；在
新模型里它是**最危险的一个值** —— 覆盖最新版，原文不可恢复。

**用户可见症状**：编辑整理版 → 保存 → 正文内容对不上（或者「我明明改的是 v3，怎么 v3 没变」）。
更常见的是**什么都看不出来**：notes 与 polished 都只有一版时，`version=1` 恰好两边都对。
**只有在一侧存在多版时才现形** —— 所以它会先被当成「偶发」。

**修复方案**

1. `perModeMetaRef`：按 mode 分桶（`{ notes: {meta, versions}, polished: {...} }`），
   四处写入点全部记桶 —— 文档加载（两个分支）、`loadVersion`。
2. **切 tab 同步 effect**：`TabRegistry.get(activeView)?.htmlMode` 命中 `notes`/`polished`
   时，把该 mode 的桶写进视图值。
   ⚠️ 判据走 `htmlMode` 而**不是** `activeView` 字面量 —— 见下。
3. **自举分支**：生成完成时 GenPanel（及恢复态的 `App.tsx`）直接写 store、**不经过本组件**，
   桶里没有记录 → 若不认领，下次从另一个 tab 切回来会读到更早的陈旧值。
4. 切文档时清空桶（`activeView` 往往不变 → effect 不重跑）。
5. `toMarkdownMeta` 收口（见 `P2-03`）。

**顺带修掉的一个继承性缺口**：polished 分支此前**从不更新** `markdownMeta` / `markdownVersions`
—— 于是「切到整理版，版本下拉显示的是笔记的版本列表」。它与本条同根（都是「meta 只为一个
mode 设计」），只是危害小到没人报过。

**⚠️ 修的过程中差点修错（值得留档）**：effect 的守卫初版我写成
`if (activeView !== 'notes' && activeView !== 'polished') return`，语义自洽、读起来没问题 ——
而**笔记 tab 的 key 是 `'summary'`**，`'notes'` 根本不在 `ActiveView` 联合类型里。
是 **tsc 报 `TS2367`（两个类型无重叠）**才暴露的。

更值得记的是：我为这条守卫写的**源码断言恰恰断定了这个错误字符串**（`/if \(mode !== 'notes'/` 是
后来才改的，初版断的是 `activeView !== 'notes'`）—— **结构守卫断得住「有没有」，断不住「对不对」**。
若那个字面量写成 `'summary2'` 之类的近形错字（tsc 看得出来才怪），它就静默永假、守卫照样全绿。
故修完后补了一条**行为断言**（`TabRegistry.get('summary')?.htmlMode === 'notes'`）把前提钉住。

**危险度判据**：数据丢失 × 静默 ÷ 触发面。触发面窄（要两侧都多版），但一旦触发**不可恢复**，
且没有任何一层会报错。

### `P1-02-artifact-version-chars-fake-zero` — `chars` 缺失填 `0`，把「不知道」写成「没有」

**证据**（[query.py](../backend/app/services/query.py) `artifact_versions`）

```python
"chars": e.get("chars", 0),        # ← 改动前
```

而同一函数的 docstring 写着 `chars` 是**读时从文件头现算的派生值**。两者合起来看就矛盾了：
派生值**可能算不出来**（文件缺失 / 头没写成），此时填 `0` 是在说「这一版有 0 个字」。
前端 `VersionSwitcher` 据此渲染「0字」—— **用户看到的是一个具体的、错误的数字**。

前端的 `undefined` 语义是「后端没给」，与「真的 0 字」严格区分（`VersionSwitcher` 的两个
测试正是断这个：缺字段**不显示**「字」，真 `0` 要显示「0字」）。**填 `0` 把这条契约从后端侧打破了。**

**修复方案**：缺失时**不给这个键** —— `**({"chars": e["chars"]} if "chars" in e else {})`。
docstring 同步订正。

**危险度判据**：这不是「少显示一个数」，是**后端主动伪造数据**，而伪造值恰好落在「用户会
当真」的那一侧。正常路径下 `chars` 恒存在，所以这个缺陷**只在异常路径现形** —— 而那正是
用户最需要准确信息的时候（产物读不出来时）。

### `P1-03-loadversion-stale-response` — 归属守卫漏了 version 这一维

**证据**（[PreviewArea.tsx](../frontend/src/components/preview/PreviewArea.tsx) `loadVersion`）

```ts
if (!md || docId !== useStore.getState().currentDocId) return    # ← 只比 docId
```

`versionLoading` 只是**异步生效**的按钮禁用，拦不住**已经发出去的请求**。用户「连点 v1、再点
v2」→ 两个请求在飞 → 先发的那个若后返回，就用 **v1 的内容覆盖刚设好的 v2**。而且
`.finally` 里旧请求还会把 `versionLoading` 放开。

**用户可见症状**：快速点两下版本，最终显示的可能是你**第一次**点的那版（且版本号高亮与内容
可能不一致 —— 高亮走 `markdownMeta`，内容走 `currentContent`）。与 `P1-01` 合起来就是
**「显示 v2、编辑却写 v1」**。

**修复方案**：守卫带全三元组（`docId` + `mode` + `version`），且识别权交给**最新**那次请求：

```ts
versionReqRef.current = { docId, mode: mdMode, version }
const isStale = () => { const r = versionReqRef.current; return !r || r.docId !== docId || ... }
...
.finally(() => { if (!isStale()) setVersionLoading(false) })
```

模式 #38：**判据要能区分它该区分的事** —— 「是不是同一个文档」在这里不够用。

### `P1-04-export-test-silent-green` — 新测试是脚本式（pytest 收集 0 条）且与生产分叉

**证据**（[test_export_by_version.py](../backend/tests/test_export_by_version.py)，改动前）

文件里是一串**模块级裸调用**加一个 `main()`。跑 `python tests/test_export_by_version.py`
能跑，跑 `pytest tests/` —— **一片绿而根本不产生用例**（`collected 0 items`，退出码 5）。
这正是 CLAUDE.md 里点名的**假绿**：本轮 25 项审计里没有一项能被这种绿抓到。

同一文件里还有第二个问题：它**自己造了一个 `AppError` 异常处理器**，而那个副本忽略了
`_explicit_status` —— 于是测试断言的状态码与**线上真正的状态码**可能不同。这是模式 #54
的教科书形态：**复刻一份表达式，分叉了不抛错、只是静默判错**。

**修复方案**

1. 改写为 pytest 风格：7 个 `def test_*` + `tmp_path_factory`（自动清理，顺带去掉原本没清理的
   `mkdtemp`）。测试名即断言内容：`test_default_is_latest` / `test_explicit_version` /
   `test_mode_and_version_are_independent_knobs` / `test_missing_version_does_not_fall_back` /
   `test_transcript_ignores_version` / `test_html_path_honors_version` /
   `test_batch_export_keeps_every_version`。
2. 把状态码判据抽成 `errors.http_status(exc)` —— **`main.py` 的异常处理器与测试共用同一份**。
   显式指定过 `status_code`（含**显式 400**）时尊重它；未显式时 `recoverable → 200`。
   ⚠️ 判据必须是「有没有显式指定」而不是「等不等于默认 400」：`status_code=400 +
   recoverable=True`（`export_custom` 就是这个组合）用后者会被误压成 200 → 前端
   `if (!r.ok)` 当成功 → **下载到损坏的 zip**。

**危险度判据**：这条本身不是产品缺陷，是**缺陷的掩体**。前两轮审计各有一半条目栽在
「测试看着过了」上 —— 一个收集不到用例的测试文件比没有测试更危险，因为它**占据着
「这条链路已经测过」的位置**。

---

## P2 — 同源漂移 / 无机制（4 项，均已修）

### `P2-01-negative-version-divergence` — 三处 `version` 判据写法不一

同一个语义（`0` = 最新，其余值查不到就报错）在三个入口三种写法：

| 入口 | 改动前 | 后果 |
|---|---|---|
| `generate.py::get_markdown` | `if version > 0:` | **负数被静默当成「最新」** |
| `export.py::export_content` | `if version and not mds:` | 正确 |
| `markdown_edit.py` | `if req.version:` | 正确 |

`version=-1` 在前者拿到最新版、在后者报 404 —— 同一份请求参数，两个答案。

**修复**：三处统一为非 0（`if version:` / `if version and not mds:`）。判据是「**非 0**」而不是
「> 0」：越界值该显式报错，不该退化成「最新」—— 「我要第 1 版，你给了我第 2 版」是最坏的一种成功。

### `P2-02-save-markdown-mutates-arg` — `save_markdown` 就地改写调用方的字典

**证据**（[store.py](../backend/app/storage/store.py)）：store 在写盘前要把**本次**定下的版本号
注入 `file_meta["identity"]["version"]`。改动前是**就地改**：

```python
identity = file_meta.get("identity") or {}
identity["version"] = version        # ← 改的是调用方的对象
file_meta["identity"] = identity
```

调用方若**拿着同一个 dict 连写两版**（第二版由第一版的版本号带过去），两版的自证头会是
**同一个版本号** —— 零报错，且只在「打开文件头看」时才可能发现。

**修复**：改成浅拷贝（`file_meta = dict(file_meta)` + `identity = dict(identity)`）——
被写的只有 `identity` 一层，连它一起拷就够。**输入参数不被函数改写**这条纪律在
「同一个 dict 会被复用」的调用形态下才有牙齿，而本轮正好引入了这种形态。

### `P2-03-to-markdown-meta-not-converged` — 收口函数没被用上

本轮新增了 [lib/markdownMeta.ts](../frontend/src/lib/markdownMeta.ts)：把
`MarkdownView → MarkdownMeta` 的映射收口为 `toMarkdownMeta()`（此前**在 5 处各写一份**，
加字段时只有一处会记得改，其余静默少字段、零报错）。

而 `PreviewArea.tsx` 内部两处**仍是内联对象字面量** —— 收口做了一半，剩下这一半正好在
**本轮新写的那条最关键的链路**上。`chars` / `source` 会静默丢失（版本下拉那一格空白、零报错）。
源码守卫 `previewVersionSync.test.ts` 加了一条断言（`not.toMatch(/setMarkdownMeta\(\{\s*id:/)`）钉住。

### `P2-04-private-domain-suffix-gap` — 代理豁免表缺保留私有域名后缀

**证据**（[core/net.py](../backend/app/core/net.py)，本轮新增）：`is_local_url` 覆盖了回环 +
私网段 + link-local，但**没有覆盖「局域网里有名字没 IP」的设备** —— NAS（`nas.local`）、
路由器、打印机、家用网关（`gateway.home.arpa`）、内网 LLM 网关（`llm-gw.internal`）。
它们会被一并塞进代理。

**修法**：加 `_LOCAL_DOMAIN_SUFFIXES = (".local", ".home.arpa", ".internal")`。
⚠️ **只收有 RFC / 官方保留依据的**：`.lan` / `.home` / `.corp` 虽然常见但无标准依据 ——
「绕过代理」是个**猜错也静默**的判据（少了代理的症状是连不上而不是报错），靠印象扩大豁免面
会把真正要走代理的网关也一并豁免掉。反面对照写进测试（`notlocal.com` / `api.localstack.com` /
`internal.example.com` —— 写成 `"local" in host` 的实现在这里红）。

顺带把唯一调用点 `_searxng_search` 的裸写法 `if proxy: client_kwargs["proxy"] = proxy`
换成 `proxy_for(base_url, proxy)` —— 它只问「配了没」，不问「目标是谁」。

---

## 记录但不修（5 项）

写在这里的**不是遗漏**，是明确决定不动的。前两轮同样有这一节 —— 它的作用是让下一次审计
不必重新论证一遍。

| 项 | 不修的理由 |
|---|---|
| `P2-05-markdown-view-type-duplication` —— `MarkdownVersion` 在 `api/generate.ts` 与 `store/types.ts` 各声明一份 | **分层所限**：`api/` 不 import `store/`（否则 API 层反向依赖状态层）。两处有注释互相指路。合并要把类型下沉到 `lib/`，收益不足以抵掉这次改动面 |
| **ingest 图片 8 位 hex 哈希碰撞**（`img_{sha1(bytes)[:8]}`） | 理论级（生日界下要同目录内 ~2^16 张同尺寸图）。与既有的 `chart_{sha1[:8]}` 同款取舍 —— 为一致性保留 8 位，不为它单独加宽 |
| **文档不存在 + 传 `version` 时**报「版本 N 不存在」而非「文档不存在」 | 两者都是 404，只是文案不够准。要分开得先查 transcript 是否存在（多一次索引访问），本轮不做 |
| `VersionSwitcher` 无 `Esc` 关闭 / `aria-haspopup` | a11y 增强，非本轮范围。组件已有关闭的鼠标路径（点外部 / 选中行） |
| **FTS5 索引 title+content 两列，无法判定命中来自哪列** | 已有措辞规避（角标写「**原文**」而不是「正文」）。要精确判定得改索引结构，不值得 |

## 已知缺口（诚实标注，**不要读成已覆盖**）

**`P1-01` 没有组件级行为测试。** 现有覆盖是：

- `previewVersionSync.test.ts` —— **源码结构守卫**（正则匹配四处接线），防的是「有人把接线
  删了 / 挪了」。文件头已明说它的局限：**「顺序错了」它抓不到**。
- `versionSwitcher.test.tsx` —— 组件行为测试，但测的是 `VersionSwitcher` 的**入参 → 渲染**，
  不覆盖「`markdownMeta` 会不会滞后」这条上游链路。
- 一条**行为断言**（`summary.htmlMode === 'notes'`）钉住 effect 判据依赖的前提。

真正的行为覆盖需要整树渲染 `PreviewArea`（`vi.mock` 掉一整个依赖图）—— 脆弱且慢，本轮未做。
**这就是为什么 `P1-01` 的修复不能只靠测试兜底**，而要在结构上消灭它（分桶 + 单一同步点）。

## 对照实验（判据有区分力的实证）

前两轮的纪律是「每条修复都要能说出：改回去会红哪一条」。本轮逐条做了：

| # | 实验 | 预期 | 实际 |
|---|---|---|---|
| A | 在迁移末尾注入 `self._rebuild_index(save=True)` | 新增的 3 条 P0 守卫全红 | ✅ 全红，**且原有 6 条全绿** —— 这正是 P0 能长期存活的原因 |
| B | 删掉 polished 分支的 `perModeMetaRef.current.polished = …` | 源码守卫第 2 条红 | ✅ |
| C | 删掉切 tab 的同步 effect | 源码守卫第 3 条红 | ✅ |
| D | 把 `startEditing` 的 mode 判据简化成直接用 `markdownMeta.version` | 源码守卫第 5 条红 | ✅ |
| E | 改回内联 `setMarkdownMeta({ id: … })` | 源码守卫第 6 条红 | ✅ |
| F | 代理豁免写 `"local" in host` | `test_public_urls_are_not_local` 的 3 条反面对照红 | ✅（已写进测试，非一次性） |

**A 是最值得记的一条**：它同时证明了「缺陷真实存在」和「原测试确实抓不到」——
后者才是审计有意义的理由。**B–E 四条对照实验全部还原**（改回正确实现后 8 条守卫全绿）。

## 门禁

`bash scripts/check-all` —— **5 通过 / 0 失败 · 46s**（tsc 5s / oxlint 1s / vitest 11s /
pytest 18s / check-cli 11s）。
