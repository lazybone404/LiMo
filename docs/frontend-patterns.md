# 前端状态管理模式

> 写于 2026-07-10 整体审查后。记录 GenPanel 任务生命周期、取消机制、状态同步的关键模式和已知陷阱。

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

```typescript
// PreviewArea.tsx — 找当前文档的进行中任务
const activeTask = currentDocId
  ? Object.entries(processingTasks).find(([k]) => k.startsWith(currentDocId + '__'))?.[1]
  : importTask?.[1]

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

**关键**：`registerAbort` 的 key 必须与 `cancelTask` 的 key 一致：
- doc 操作：用裸 `docId`（如 `"abc123"`）
- 导入操作：用 `"__import__"`（固定字符串）

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

### 重转录（handleRetranscribe）

```
开始 → setRetranscribing(true) + setProcessing(docId + '__retranscribe', task)
    → createAbort(docId)
    → retranscribe(...)  // SSE 端点
    ↓
完成 → setRetranscribing(false) + setProcessing(key, null) + 刷新内容
失败 → setError(msg) + setRetranscribing(false) + setProcessing(key, null)
```

**共同点**：
1. abort signal 来自 `createAbort(docId)`，注册在裸 `docId`
2. store key 是 `docId + '__' + kind`
3. 完成/失败都用 `setProcessing(key, null)` 精确清理（不用 `clearDocTasks`）
4. 所有回调用 `isSameDoc(capturedId)` 做 stale-check

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
// client.ts — streamGenerate
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
// client.ts
export function apiUrl(path: string): string {
  return path.startsWith('http') ? path : `${BASE}${path}`
}
// Tauri: BASE = 'http://127.0.0.1:7413/api'
// Browser: BASE = '/api'
```

**规则**：所有 HTTP 请求必须走 `apiUrl()` 或 `apiFetch`（内部走 `apiUrl`）。裸 `fetch('/api/...')` 在 Tauri WebView2 下会失败。

**已知例外**：`ExportPanel.tsx` 曾有一个局部 `apiPostUrl` 重复实现（已统一为 `apiUrl`）。

---

## 7. Store 持久化

Zustand `persist` middleware 只持久化 3 个字段到 localStorage：

| key | 字段 | 用途 |
|-----|------|------|
| `mbw_lastDocId` | currentDocId | 恢复上次选中的文档 |
| `mbw_lastView` | activeView | 恢复上次的预览 tab |
| `mbw_lastConvId` | currentConversationId | 恢复上次的对话 |

LLM 设置、provider、引擎配置都在后端 `~/.mybigwork/config.yaml`，不由前端持久化。

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
