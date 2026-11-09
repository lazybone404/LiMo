# 短期待办

**这里放什么**：已经决定要做、做法清楚、近期就会动手的事。做完直接从本文件删掉。

**这里不放什么**：

- 还没定要不要做的方向、以及**等触发条件**的重构 → [roadmap.md](roadmap.md)。
  roadmap 只留大方向与「继续这么长下去会变贵」的结构债，不排期。
- 已经做完的记录 → roadmap 各域的 ✅ 行与 [CHANGELOG.md](CHANGELOG.md)。

⚠️ **同一条只待在一个地方。** 本文件与 roadmap 各写一份，等于两处各自记着「这件还没做」，
做完时漏删一处，下个接手的人就会重复做一遍。

## 排队中

### 1. 后端任务队列：长操作统一托管（2026-11-09 决议）

**为什么**：长操作的状态住在组件或前端 store 里时，组件卸载 / 页面重载会丢进度；更要紧的是
两处能**重复触发** —— 插件依赖安装（[PluginSettings.tsx:116](../frontend/src/components/settings/PluginSettings.tsx#L116)，
pip 分钟级，后端 [plugins.py:187](../backend/app/routers/plugins.py#L187) 无锁，回到设置页按钮复位可再点一次）
与合成（[DocLibrary.tsx:1206](../frontend/src/components/doc-library/DocLibrary.tsx#L1206)，
一次计费 LLM 调用，`synthStage` 丢了就能对同一批文档再点一次）。

**做法**：操作有 id + 状态在后端 + 读口 + 幂等键。三步，各自可独立交付：

1. `{store.root}/jobs.db`（**独立文件**，不并进 `search.db` —— 那边有损坏自愈，重建会清掉任务）
   + 存储层 + worker + kind 注册表 + `POST/GET /api/jobs` 与 `POST /api/jobs/{id}/cancel` + 测试。
   关键列：`id / kind / idempotency_key / payload / status / progress / message / result / error /
   attempts / created_at / started_at / finished_at`。幂等靠唯一索引
   （`ON jobs(idempotency_key) WHERE idempotency_key IS NOT NULL`），撞约束时返回已有那条。
   worker 认领用一条 `UPDATE ... WHERE id=? AND status='queued'` 原子确认。
   沿 [vector_worker.py](../backend/app/services/vector_worker.py#L108) 的 loop 形态与
   [search.py](../backend/app/core/search.py#L85) 的 WAL / 必关连接约定。
2. 前端 `jobsSlice` + `TaskPanel` 接入 + **插件依赖安装迁过去**。验收 = 点安装、切到别的页面，
   任务面板那行还在走，按钮不再亮。
3. 合成与分类提交迁过去。合成幂等键 = `synth:{排序后的 docIds}:{模式}`，`max_attempts = 1`。

**恢复语义按 kind 分**（最容易做错的一处）：重跑无副作用的（插件安装、重索引）→ 启动时把
`running` 改回 `queued`；重跑要再花钱的（合成、分类）→ 标 `failed` 并写「进程中断」，不自动重跑。

**不改的**：重新索引已有 `VectorWorker` + 状态端点；导入 / 转录 / 生成已在 `processingTasks`。

### 2. Rust 更新状态快照 + 读口

更新下载的进度现在只活在前端 store，页面重载即归零（Rust 侧只有下完后的 `PREPARED`，
见 [updater.rs:60](../frontend/src-tauri/src/updater.rs#L60)）。给 `updater.rs` 加一份下载途中持续
更新的快照（阶段 / 版本 / 已下字节 / 总数 / 上次错误）+ `get_update_state` 命令，`updateSlice`
挂载时读一次。约一百行，不动下载逻辑。验收 = 下载到一半刷新页面，进度仍然显示。

### 3. `docs/patterns.md` 第 73 条：状态的寿命判据

判据一句话：**组件卸载以后，这份状态还能从哪里读回来？** 答不上来就是放错了位置。
三条对照：[SearchIndexCard](../frontend/src/components/settings/SearchIndexCard.tsx#L121)
（挂载重读 → 局部 state 可以）、`UpdateCard`（曾是唯一副本 → 必须搬）、
`ExportPanel` 与分类预览（卸载即 abort，正确）。测试形态：挂载 → 启动 → 卸载 → 重挂载 →
断言状态还在。同批记进 [constants-home.md](constants-home.md)：长操作的前端入口只有一个。

