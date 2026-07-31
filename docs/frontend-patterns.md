# 前端状态管理模式

> 写于 2026-07-10 整体审查后（2026-08-01 更新 store 拆分）。记录 GenPanel 任务生命周期、取消机制、状态同步的关键模式和已知陷阱。

---

## 0. Store 结构（2026-08-01 拆分为 slices）

`frontend/src/store/` 目录：

| 文件 | 内容 |
|------|------|
| `types.ts` | 全部共享类型 + 具名类型（`ActiveView`/`MarkdownMeta`/`ImageResult` 等）+ `Store` 交集 |
| `themeSlice.ts` | 主题六轴（theme/accent/texture/font/base/applyPreset），完全自包含 |
| `docsSlice.ts` | 文档库（docs/currentDocId/searchQuery/activeTag） |
| `contentSlice.ts` | 预览内容 + AI 审核 + streamTick |
| `tasksSlice.ts` | importProgress/importStages/imageResults/processingTasks/taskPanelOpen |
| `chatSlice.ts` | 对话管理 + 生成状态 + AI 浮动对话 |
| `uiSlice.ts` | slots/activeView/backendReady/contentWidth/capabilities |
| `toastSlice.ts` | toasts + `nextMsgId`（模块级 msgCounter） |
| `index.ts` | 组合层：`create<Store>()((...a) => ({...createXSlice(...a)}))` + 模块副作用（apply 主题轴 + matchMedia 监听） |

**规则**：
- slice 创建者签名 `StateCreator<Store, [], [], XSlice>`，`Store` 为各 slice 交集
- **action 零跨域引用**——新增域 = 新建 slice 文件，`index.ts` 加一行组合，消费方零改动
- 模块初始化副作用（apply 五主题轴 + matchMedia 监听）**必须留在 index.ts**，不可移入 slice（监听器需 `useStore.getState()`，移入会制造运行时循环依赖）
- 持久化是手写 localStorage（见 §7），无 persist 中间件

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
| 事实验证 | docId | `fact_check` | `abc123__fact_check` |
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

## 6. apiUrl 模式

```typescript
// api/core.ts（2026-08-01 按域拆分：core + capabilities/transcripts/ingest/generate/chat/settings/export）
export function apiUrl(path: string): string {
  return path.startsWith('http') ? path : `${BASE}${path}`
}
// Tauri: BASE = 'http://127.0.0.1:7413/api'
// Browser: BASE = '/api'
```

**规则**：所有 HTTP 请求必须走 `apiUrl()` 或 `apiFetch`（内部走 `apiUrl`）。裸 `fetch('/api/...')` 在 Tauri WebView2 下会失败。

**注意**：`BASE` 在 core.ts 导出但**不经过 barrel**（`client.ts` 显式 re-export 公共符号）——域文件从 `./core` import，外部禁止直接用 `BASE`。

---

## 7. Store 持久化

**无 persist 中间件**——手写 localStorage，`mbw_*` 前缀共 9 个 key：

| key | 写入位置 | 用途 |
|-----|---------|------|
| `mbw_theme` | lib/theme.ts | 明暗模式（getSaved*/save* 模式） |
| `mbw_accent` | lib/accent.ts | 主色调 |
| `mbw_texture` / `mbw_font` | lib/texture.ts | 纹理 / 字体 |
| `mbw_base` | lib/theme-base.ts | 界面基调 |
| `mbw_lastDocId` | docsSlice selectDoc | 恢复上次选中的文档 |
| `mbw_lastView` | uiSlice setActiveView | 恢复上次的预览 tab |
| `mbw_lastConvId` | chatSlice setCurrentConversationId | 恢复上次的对话 |
| `mbw_contentWidth` | uiSlice cycleContentWidth | 阅读宽度 |

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
