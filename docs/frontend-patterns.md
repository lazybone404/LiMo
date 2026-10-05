# 前端状态管理模式

> 写于 2026-07-10 整体审查后（2026-08-01 更新 store 拆分；2026-08-14 更新 store 表/api 域文件/mbw key/探活与缺 key 提示；2026-10-05 更新 docsSlice 标签页状态、api 域文件计数 16、mbw_reading 字号、§8 补两条陷阱）。记录 GenPanel 任务生命周期、取消机制、状态同步的关键模式和已知陷阱。

---

## 0. Store 结构（2026-08-01 拆分为 slices）

`frontend/src/store/` 目录：

| 文件 | 内容 |
|------|------|
| `types.ts` | 全部共享类型 + 具名类型（`ActiveView`/`MarkdownMeta`/`ImageResult` 等）+ `Store` 交集 |
| `themeSlice.ts` | 主题六轴（theme/accent/texture/font/base/applyPreset），完全自包含 |
| `docsSlice.ts` | 文档库（docs/currentDocId/searchQuery/activeTag）+ **文档多标签页**（docTabs/pendingOpenMode/pendingClose/commitTabChange/cancelTabChange，**延后提交**见 §8） |
| `contentSlice.ts` | 预览内容 + streamTick |
| `tasksSlice.ts` | importProgress/importStages/imageResults/processingTasks/taskPanelOpen |
| `chatSlice.ts` | 生成状态（generating）——对话管理在独立 chatStore |
| `chatStore.ts` | **独立对话状态机**（不并入主 store）：ChatMsg 模型 + 流式状态机 + 会话 CRUD + 上下文管理（budget/usage/压缩提示/clearContext） |
| `streamBuffers.ts` | **流式高速缓冲区**（独立模块级 Map，非 slice）：LLM token 高频写入（20-50/s）→ `scheduleFlush` 120ms 去抖 → `bumpStreamTick` 驱动 PreviewArea 重读。避免高频写 store 产生大量快照 |
| `uiSlice.ts` | slots/activeView/backendReady/**backendStatus**/contentWidth/capabilities + AI 浮窗（aiVisible/aiPos/aiSize，位置/尺寸持久化） |
| `toastSlice.ts` | toasts + `nextMsgId`（模块级 msgCounter） |
| `index.ts` | 组合层：`create<Store>()((...a) => ({...createXSlice(...a)}))` + 模块副作用（apply 主题轴 + matchMedia 监听） |

**规则**：
- slice 创建者签名 `StateCreator<Store, [], [], XSlice>`，`Store` 为各 slice 交集
- **action 零跨域引用**——新增域 = 新建 slice 文件，`index.ts` 加一行组合，消费方零改动
- 模块初始化副作用（apply 五主题轴 + matchMedia 监听）**必须留在 index.ts**，不可移入 slice（监听器需 `useStore.getState()`，移入会制造运行时循环依赖）
- 持久化是手写 localStorage（见 §7），无 persist 中间件

### 探活与缺 key 提示（2026-08-13 新增）

- **`lib/health.ts` 持续探活**：`startHealthPoll()`（App 挂载时调用）10s 轮询 `/api/health` → 状态写 `uiSlice.backendStatus`（`ready`/`down`/`crashed` 三态）。连续 2 次失败 → `down`（「后端服务异常，正在重试」）；连续失败超 30s → `crashed`（「后端已崩溃，请重启应用」）。与启动探活（App.tsx 的「后端未就绪加载屏」）职责分离。
- **缺 key 检测**：基于 `capabilities.missingKeys`（后端返回缺失的 LLM/DashScope key），ImportSection 导入图片/视频时内联提示条（`mbw_dashscope_hint_dismissed` 记忆已关），chatStore 首条消息发送前 toast 提示 LLM key（`mbw_llm_hint_dismissed`）。OnboardingWizard 第三屏基于 missingKeys 自动跳过已配项。

---

## 1. processingTasks 键格式

Zustand store `processingTasks: Record<string, ProcessingTask>` 的键统一为：

```
{id}__{kind}
```

| 场景 | id | kind | 完整键示例 |
|------|-----|------|-----------|
| 重转录 | docId | `retranscribe` | `abc123__retranscribe` |
| 生成 | docId | `generate` | `abc123__generate` |
| 图片处理 | docId | `process` | `abc123__process` |
| 导入 | `__import__{seq}` | `import` | `__import__0__import` |

**规则**：
- doc 级任务：`docId + '__' + kind`
- 导入任务：`'__import__' + seq + '__import'`（seq 是自增计数器，支持并发导入）
- 永远不会有裸 `docId` 作为键

### 查找 activeTask

> **已更新（2026-07-26）**：从单值 `activeTask`（`find()`）改为数组 `activeTasks`（`filter()`），支持同文档多任务并发。

```typescript
// PreviewArea.tsx — 找当前文档的进行中任务
const activeTasks = currentDocId
  ? Object.entries(processingTasks)
      .filter(([k]) => k.startsWith(currentDocId + '__'))
      .map(([, v]) => v)
  : Object.entries(processingTasks)
      .filter(([k, v]) => k.startsWith('__import__') && v.kind === 'import')
      .map(([, v]) => v)

// PreviewArea.tsx — 判断视图是否在加载中（ProcessingPlaceholder 显示 activeTasks[0]）
const isViewProcessing = (view: string) => {
  const def = TabRegistry.get(view)
  if (!def?.processingKinds) return false
  return activeTasks.some(task => {
    // process kind 需要额外匹配 mode（图片处理 tab）
    if (task.kind === 'process') return def.processingKinds!.includes('process') && view === task.mode
    return def.processingKinds!.includes(task.kind)
  })
}

// GenPanel.tsx — 同步 TaskPanel 外部取消
useEffect(() => {
  if (currentDocId && !Object.keys(processingTasks).some(k => k.startsWith(currentDocId + '__'))) {
    // 重置 busy 状态
  }
}, [processingTasks, currentDocId])
```

**陷阱**：永远不要用 `processingTasks[currentDocId]` 判断——这个键永远不存在。

### 清理任务

```typescript
// 精确清理（推荐）—— 只清一个操作
setProcessing(docId + '__generate', null)

// 批量清理 —— 清文档的所有操作（用于 cancelOp / 文档切换）
store.clearDocTasks(docId)  // 前缀匹配 `${docId}__`
```

**陷阱**：`clearDocTasks` 只清 store 状态，不 abort 网络请求。必须先 `cancelTask(docId)` 再 `clearDocTasks(docId)`。

---

## 2. 取消机制（AbortController + taskManager）

### 注册

```typescript
// GenPanel.tsx — createAbort
import { registerAbort } from '../../api/taskManager'

const createAbort = (docId: string) => {
  const ctrl = new AbortController()
  registerAbort(docId, ctrl)  // 存入全局 Map
  return ctrl.signal
}
```

**关键**（2026-07-31 修复）：`registerAbort` 接受**完整 taskKey**（如 `"abc123__generate"`）并内部归一化——doc 任务按 base docId 存储（同文档重复注册自动 abort 旧任务），导入任务按 `__import__` 前缀独立存储（多导入并发不互杀）。`cancelTask` 的 key：
- doc 操作：裸 `docId`（如 `"abc123"`，归一化后命中）
- 导入操作：`"__import__"`（固定字符串，命中全部导入）

### 取消（GenPanel cancelOp）

```typescript
const cancelOp = () => {
  // 1. 先 abort 网络请求
  if (Object.keys(processingTasks).some(k => k.startsWith('__import__'))) {
    cancelTask('__import__')  // 导入用固定 key
  }
  if (currentDocId) cancelTask(currentDocId)  // doc 用裸 id

  // 2. 再清 store 状态
  if (currentDocId) clearDocTasks(currentDocId)

  // 3. 重置本地 useState
  setImporting(false); setGenerating(false); // ...
}
```

### 取消（TaskPanel 单任务按钮）

```typescript
// TaskPanel.tsx — docId 来自 Object.entries(processingTasks)，是完整 key
const baseKey = docId.startsWith('__import__')
  ? '__import__'
  : docId.includes('__') ? docId.slice(0, docId.indexOf('__')) : docId
cancelTask(baseKey)  // 用提取后的基础 key
setProcessing(docId, null)  // 用完整 key 清理 store
```

**陷阱**：TaskPanel 中的 `docId` 是 `"abc123__generate"`（完整键），不能直接传给 `cancelTask`。必须提取基础 key。

---

## 3. 操作生命周期

### 生成（handleGenerate）

```
开始 → setGenerating(true) + setProcessing(docId + '__generate', task)
    → createAbort(docId)
    → streamGenerate(...)
    ↓
onChunk → setStreamingContent / setIsStreaming
onDone  → setGenerating(false) + setProcessing(key, null) + Toast
onError → setError(msg) + setGenerating(false) + setProcessing(key, null)
onCancelled → setGenerating(false) + setProcessing(key, null)
```

### 重转录 / 图片处理（useTaskRunner，2026-08-01 抽取）

`handleRetranscribe` / `handleProcessImage` 的公共骨架（前置检查 → capturedId → taskKey → abort → setProcessing → await → stale 检查 → 错误处理 → finally 清理）已抽取为 `hooks/useTaskRunner.ts`：

```typescript
const { runTask } = useTaskRunner()
void runTask({
  taskKey: capturedId + '__retranscribe',        // 同时用于 stale 检查与 setProcessing
  taskInfo: { kind: 'retranscribe' },
  prepareAbort: createAbort,
  onStart: () => { setError(''); setImportError('') },
  run: (signal) => retranscribe(capturedId, signal),
  onSuccess: async (result) => { /* 成功副作用 */ },
  onFail: (msg) => { setError(msg); addToast(msg, 'error') },
})
```

**共同点**：
1. store key 是 `docId + '__' + kind`
2. 完成/失败都由 hook 用 `setProcessing(key, null)` 精确清理（前缀树形删除，不用 `clearDocTasks`）
3. AbortError 静默返回（用户取消不报错）
4. hook 只在 `run()` 完成后查一次 stale——**onSuccess 内部多段 await 之后的 `isSameDoc` 检查必须逐字保留**（handleRetranscribe 原有 3 处）
5. `handleGenerate`（callback 风格 + 流式缓冲生命周期）与 `handleImport`（分组 key + preOcr 子任务）不套用 hook

---

## 4. 操作互斥

```typescript
// GenPanel.tsx
const lockOtherOps = retranscribing || imageProcessing

// 生成按钮：被重转录/图片处理锁住
disabled={generating || lockOtherOps || ...}

// 重转录按钮：被生成锁住
disabled={retranscribing || generating}

// 图片处理按钮：不需要互斥（图片类型的文档不会同时显示重转录按钮）
disabled={imageProcessing || !currentDocId}
```

**说明**：`lockOtherOps` 不含 `importing`，因为 UI 结构天然隔离——导入发生在 ImportSection，不与其他操作竞争同一文档。不对称是刻意的。

### Ctrl+G 快捷键保护

```typescript
// App.tsx
if (useStore.getState().generating || Object.keys(useStore.getState().processingTasks).length > 0) {
  useStore.getState().addToast('操作进行中，请完成或取消后再切换面板', 'info')
} else {
  toggleGenPanel()
}
```

---

## 5. SSE 断连检测

```typescript
// api/generate.ts — streamGenerate
let receivedDoneOrError = false
const wrappedOnDone = (result) => { receivedDoneOrError = true; onDone(result) }
const wrappedOnError = (msg) => { receivedDoneOrError = true; onError(msg) }

// SSE 循环结束后
if (!receivedDoneOrError) {
  onError('连接意外断开，请重试')  // 防止 generating 永不复位
}
```

---

## 6. apiUrl / appUrl 模式

`api/` 目录 16 个文件（2026-08-01 按域拆分；2026-10-05 重新计数）：

| 文件 | 职责 |
|------|------|
| `core.ts` | `BASE`/`apiUrl()`/`appUrl()`/`apiFetch`/错误处理 —— **不经过 barrel**，域文件从 `./core` import |
| `client.ts` | barrel，显式 re-export 公共符号（禁止直接用 `BASE`） |
| `capabilities.ts` | 能力元数据（fileTypeMap/引擎/模型/missingKeys），驱动前端自动发现与缺 key 检测 |
| `transcripts.ts` / `settings.ts` | 转录结果 / 配置读写 |
| `ingest.ts` / `generate.ts` | 导入（含 SSE 进度）/ 生成（流式 + 取消） |
| `chat.ts` / `export.ts` / `references.ts` / `plugins.ts` | 对话（SSE）/ 导出 / 参考文件 / 插件管理 |
| `epub.ts` / `search.ts` / `synthesis.ts` | EPUB 一键解读 / FTS 全文搜索 / 多篇合成（SSE） |
| `taskManager.ts` | `registerAbort`/`cancelTask` 全局 AbortController Map |
| `sse.ts` | `parseSSEStream` 统一 SSE 解析（fetch → ReadableStream → 事件回调），各流式端点复用 |

```typescript
// api/core.ts
export function apiUrl(path: string): string {
  return path.startsWith('http') ? path : `${BASE}${path}`
}
// Tauri: BASE = 'http://127.0.0.1:7413/api'
// Browser: BASE = '/api'

// 应用内图片/静态资源 URL（MarkdownImage / DocLibrary / VideoBanner 必须用它）
export function appUrl(path: string): string {
  if (!path || path.startsWith('http')) return path
  if (!isTauri) return path
  if (path.startsWith('/api/')) return apiUrl(path.slice(4))
  return `http://127.0.0.1:7413${path}`   // /static/ 等 → 绝对 URL
}
```

**规则**：
- 所有 **HTTP 请求** 必须走 `apiUrl()` 或 `apiFetch`（内部走 `apiUrl`）。裸 `fetch('/api/...')` 在 Tauri WebView2 下会失败。
- 所有**图片/静态资源**（`<img src>`、封面）必须走 `appUrl()`——打包端无 Vite proxy，`/api/` 和 `/static/` 路径需转绝对 URL。
- `BASE` 在 core.ts 导出但**不经过 barrel**——域文件从 `./core` import，外部禁止直接用 `BASE`。

---

## 7. Store 持久化

**无 persist 中间件**——手写 localStorage，`mbw_*` 前缀共 16 个 key：

| key | 写入位置 | 用途 |
|-----|---------|------|
| `mbw_theme` | lib/theme.ts | 明暗模式（getSaved*/save* 模式） |
| `mbw_accent` | lib/accent.ts | 主色调 |
| `mbw_texture` / `mbw_font` | lib/texture.ts | 纹理 / 字体 |
| `mbw_texture_intensity` | lib/texture.ts | 纹理强度（滑条 10-100） |
| `mbw_base` | lib/theme-base.ts | 界面基调 |
| `mbw_lastDocId` | docsSlice selectDoc | 恢复上次选中的文档 |
| `mbw_lastView` | uiSlice setActiveView | 恢复上次的预览 tab |
| `mbw_lastConvId` | ChatCore 切换会话时 | 恢复上次的对话（chatStore 内存不持久化，组件层读写） |
| `mbw_aiPos` / `mbw_aiSize` | uiSlice setAiPos/setAiSize | AI 浮窗位置/大小记忆 |
| `mbw_contentWidth` | uiSlice cycleContentWidth | 阅读宽度 |
| `mbw_llm_hint_dismissed` | chatStore | 缺 LLM key 首提 toast 已关闭 |
| `mbw_dashscope_hint_dismissed` | ImportSection | 缺 dashscope 内联提示条已关闭 |
| `mbw_docLibPrefs` | doc-library/docLibraryPrefs.ts | 文档库偏好（密度/排列/排序/折叠小节/筛选区高度） |
| `mbw_reading` | lib/reading-prefs.ts | 沉浸阅读版式（行高 / 每行字数 / 段落区隔 / **正文字号缩放 fontScale**） |

**规则**：lib 层的持久化在 slice 的 setter 中调用（如 `setTheme` → `saveTheme` → `applyTheme` → `set`）；action 内的 localStorage 读写需 try/catch（quota）。LLM 设置、provider、引擎配置都在后端 `~/.limo/config.yaml`，不由前端持久化。

---

## 8. 关键陷阱速查

| 陷阱 | 后果 | 修复 |
|------|------|------|
| `processingTasks[docId]` 裸键查找 | 永远找不到，useEffect 每帧触发 | 用 `startsWith(docId + '__')` |
| `cancelTask(fullKey)` TaskPanel | abort 失败，请求继续 | 提取基础 key 再传 |
| `cancelOp` 不调 `cancelTask` | UI 清除但请求继续 | cancelTask → clearDocTasks 顺序 |
| `clearDocTasks` 替代 `setProcessing(key, null)` | 误杀同文档的其他并发任务 | 精确 key 清理 |
| `apiKey ?? current` 空字符串 | `""` 不是 nullish，覆盖真实 key | `(val \|\| undefined) ?? current` |
| `fetch()` 替代 `apiFetch()` | HTTP 错误被忽略，Toast 误导 | 统一用 `apiFetch` |
| 把「不改 `currentDocId` 的关闭」也塞进 `pendingClose` | 标签**永远关不掉且零报错**（pending 只被 `loadDocContent` 消费，而那条路径上它不会跑） | 三个 close 动作按「是否改变 `currentDocId`」各分两支 |
| React `onWheel` 里调 `preventDefault()` | passive 注册下无效 → WebView2 把整页缩放了 | 原生 `addEventListener('wheel', h, { passive: false })`，挂目标容器 |
| 正文新字号写 `em`，或把 `fontSizeScale` 乘进版宽换算 | 字号与外层 em **相乘**随宽度档漂移 / 版宽跟着宽度模式变 | 写 `px`；版宽只乘 `fontScale`（`measurePxOf`） |
