# 前端开发手册（状态 / 布局 / 交互模式）

> 写于 2026-07-10 整体审查后（2026-08-01 更新 store 拆分；2026-08-14 更新 store 表/api 域文件/mbw key/探活与缺 key 提示；2026-10-05 更新 docsSlice 标签页状态、api 域文件计数 16、mbw_reading 字号、§8 补两条陷阱）。记录 GenPanel 任务生命周期、取消机制、状态同步的关键模式和已知陷阱。（2026-11-07 从 CLAUDE.md 并入「前端布局」与「前端关键模式」两节，见 §9 / §10。）

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
| `updateSlice.ts` | **应用更新流程**（2026-11-08）：`phase` 八阶（idle/checking/up-to-date/available/downloading/ready/installing/failed）+ 进度 + 跳过版本 + 三个动作入口。三个视图共用：设置卡（完整流程）/ 任务面板（下载·已下载·失败各一行）/ 顶栏提示。⚠️ 它**不能**留在组件里：下载几分钟，卡片一卸载 `aliveRef` 就把进度回调全丢了，而 Rust 侧照常下完并重启 —— 见 [desktop-shell.md](desktop-shell.md) 的更新小节 |
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
| `mbw_reading` | lib/reading-prefs.ts | 沉浸阅读版式（行高 / 每行字数 / 段落区隔 / **正文字号缩放 fontScale**）。⚠️ 三组快速预设（`READING_PRESETS`）**不另存 key**，选中态由 `matchReadingPreset(reading)` 比较三项值得出 |

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

---

## 9. 布局：活动栏 / 插槽 / 标签栏 / 浮窗


```
[ActivityBar] [GenPanel | DocLibrary] | [PreviewArea]
```
- **左侧活动栏**（`ui/ActivityBar.tsx`，2026-10-10）：常驻最左的竖排图标列。面板项**从 `SlotRegistry.grouped().left` 派生**（顺序即注册顺序）+ 两个**动作项**（AI 对话 / 设置，它们不是 slot，故在 store 之外硬编码）。⚠️ **顶栏的面板按钮已全部删除**——活动栏是唯一入口，刻意不留第二套（两处各写一遍必然漂移，模式 #54）
- **左侧两面板默认互斥**（`lib/layout-prefs.ts` 的 `leftPanelsExclusive`，localStorage `mbw_layout`）：同一时刻只展开一个，由活动栏图标切换；**再按一次是关闭、不露出另一个**（VS Code 语义）。改回「允许并排」在 设置 → 外观 → 侧栏布局。⚠️ **互斥靠 `uiSlice.activeLeftPanel` 这个「选择器字段」，不是「打开甲时遍历同侧把乙收起」**——后者可被任何直接改单个 slot 的调用点绕过，而这样的调用点有四处（`openDocLibSearch` / DocLibrary 右键「重新生成」/ GenPanel 与 DocLibraryToolbar 的面板自关闭），漏一处就露出两个面板。统一入口：`openPanel` / `closePanel` / `togglePanel`（`toggleGenPanel` / `setGenPanelOpen` 保留为薄委托别名）
- **互斥模式下关掉的面板「连 PanelStrip 都不渲染」**——一次只该有一个面板，34px 窄条此时没有意义；并排模式保留现状（收起 = 窄条）
- 可拖拽 resize，50–600px，<80px 自动折叠为 PanelStrip；**默认宽 240px**（`lib/slotMetrics.ts` 的 `PANEL_DEFAULT_WIDTH`，2026-11-08 从 260 收窄；注册表 `defaultWidth` 与 `uiSlice.slots` 初值都取它，改一处即可）
- **界面外框高度 2026-11-08 收窄一轮**：顶栏 `h-10`（原 `h-12`）、文档标签标签 `h-7`（原 `h-8`）、预览区工具栏 `py-1` + 内部按钮 `py-1`（原 `py-2.5` / `py-1.5`），三条合计约 106px（原约 136px）；活动栏 `w-10`（原 `w-12`）。理由是这些入口的重要性低于正文，视线应落在预览内容上。⚠️ **图标按钮热区不得小于 24×24**——缩到 `w-6 h-6` 就是下限，再小要靠 `p-*` 补回来；⚠️ 标签栏的横向滚动（`overflow-x-auto`）与标签 `min-w` 不动，窄窗口下仍能滚。
- **预览区顶部有文档标签栏**（`preview/DocTabBar.tsx`）：VS Code 双标签模型——单击=**预览标签**（斜体、可被下一次单击替换，全组至多一个），双击=钉成**常驻**，已打开的文档直接切过去；不做溢出下拉 / 拖拽排序 / 标签组持久化（基础版，溢出走横向滚动）。⚠️ **`currentDocId` 仍是唯一真相源**（不另存 `activeTabId`），激活态由 `tab.docId === currentDocId` 派生；⚠️ **延后提交**：导航意图先进 `pendingOpenMode`/`pendingClose`，`loadDocContent` **首行**才 `commitTabChange` —— 于是「取消未保存弹窗」天然等于什么都没发生（模式 #62）；⚠️ **与本页签名易混**：`tabRegistry` + `activeView` 是一篇文档**内部**的视图切换，标签栏是**文档之间**的，两者无关
- **正文字号缩放**：`reading-prefs.ts` 的 `fontScale`（默认 1.0 = 现状 16px，归入既有「阅读版式」族）→ `--read-font-size` → `index.css` 的 `.preview-area .markdown-body { font-size }`（特异性 0,2,0 压过 vendor 硬编码的 16px，**不用 `!important`**——那是「颜色覆盖」的做法）。快捷键 `Ctrl+滚轮`（**原生** `addEventListener(..., { passive: false })`，React 的 `onWheel` 拦不住）/ `Ctrl+=`/`+`/`-`/`_`/`0`。⚠️ 三个必须：写 **px**（em 会与外层 `.preview-content` 的内联 em 相乘）、版宽换算走 `measurePxOf(measure, fontScale)` **同源**、作用域限定 `.preview-area`（模式 #64）
- **阅读版式预设（2026-11-08）**：`READING_PRESETS`（随应用 / 书页 / 紧凑）一次设好**行高 + 每行字数 + 段落区隔**三项，值住在 `lib/reading-prefs.ts`。⚠️ **选中态不存**：界面用 `matchReadingPreset(reading)` 与三项值**全等**比较得出，否则「当前是哪个预设」这个标记位会在手调任一旋钮后过期（显示「书页」选中而值不是）；「随应用」直接取 `READING_DEFAULTS` 的字段，不写第二份字面量。⚠️ **刻意不含 `fontScale`**（字号取决于屏幕与视力，不由预设决定），也**不进「一键主题包」**（换主题包不该连带重置版式）
- **对话字号是另一份（2026-10-12）**：`lib/chat-font.ts`（key `mbw_chatFont`）→ `--chat-font-scale`（无单位倍率）→ `index.css` 的 `.chat-msg-sm|md|lg`（`calc(<基准px> × var(--chat-font-scale))`）。⚠️ **基准 px 在 `ai-chat/scale.ts`（随浮窗宽度切档）、倍率在 chat-font.ts** —— 两个维度各住一处，别把 px 搬进 JS 变量或把倍率搬进 scale。⚠️ **与阅读字号刻意不共用**（两个宿主两种排版：宽屏长文 vs 窄浮窗气泡），但**档位换算复用 `reading-prefs.nextFontScale` / `FONT_SCALE_RANGE`**（`toBe` 身份断言钉住）。⚠️ 只有滚轮一条入口，键盘 `Ctrl+±/0` **仍只调阅读字号**
- 快捷键：`Ctrl+G` 切到生成面板（⚠️ **语义随布局偏好变**：互斥下没开就打开、开着就关闭且**不露出文档库**；并排下与改造前一致的「切换」），`Ctrl+I` 开关 AI 浮窗，`Ctrl+W` 循环阅读宽度，`Ctrl+K` 展开文档库并聚焦搜索框（**全局劫持，不做 `activeElement` 守卫**——与 Ctrl+A 相反，那个必须让位给输入框内全选），`Ctrl+\` 开关沉浸阅读，`Ctrl+/` 快捷键一览，字号三键见上一条。⚠️ **清单真相源是 [App.tsx::handleAppKeyDown](../frontend/src/App.tsx#L43)**（全部 `case` 即全集）——**别照抄本行**，它是摘要不是源
  - ⚠️ **全局快捷键现在有四份同源，加一个键要一起改**（2026-11-03 补）：① `App.tsx` 的 `case`（真正干活）② `src-tauri/src/lib.rs::webview_keys::is_app_shortcut`（打包版从 WebView2 加速键手里放行 —— **漏了 = 开发模式正常、打包版没反应、零报错**）③ `test/appShortcutKeys.test.ts` 的 `KEY_TO_VK` ④ `lib/shortcuts.ts`（帮助面板的展示表）。①②③ 由该测试双向钉住（模式 #65）；④ 由 [shortcutHelp.test.ts](../frontend/src/test/shortcutHelp.test.ts) **单向**钉住（App.tsx 认的键必须在表里；反向不强制 —— 局部快捷键要不要收录是产品取舍）。⚠️ **同一个物理键的 Shift 变体共用同一个 VK**（`+` 是 Shift+`=`、`?` 是 Shift+`/`）⇒ 前端要两个 `case`，Rust 侧只需一个常量。⚠️ 展示表存 `e.key` **字面量**不是 `Ctrl+G` 这种展示串（展示由 `formatShortcut()` 负责，混在一起守卫就核对不上）；一条动作可有多个等价键，展示只取第一个
- **AI 对话为浮窗（唯一形态，2026-08-04 移除插槽面板）**：`FloatingAI` 覆盖式浮窗，可拖拽（边界钳制）/resize（不超视口），位置/大小持久化 localStorage（`mbw_aiPos`/`mbw_aiSize`），内部栏（会话列表/导航）可拖拽调宽 + 拖 <80px 自动折叠。**消息区三条交互（2026-10-12，全在 `MessageList` / `ChatMarkerContent`）**：① `Ctrl+滚轮` 调**对话自己的**字号（见上「对话字号是另一份」）；② 上滚后右下角「回到最新」按钮 —— ⚠️ **显隐与 `stickToBottom` 同源**（同一个 `< 40px` 判据、同一次 `onScroll` 更新），各写一套会在阈值附近互相矛盾；③ 代码块 hover 右上角一键复制 —— ⚠️ 挂 **`pre`** 不挂 `code`（`react-markdown` v10 已无 `inline` prop，块级是 `pre > code` 而行内是裸 `code`），且**刻意不复用产物侧 `CodeBlock`**（那个带 `SyntaxHighlighter` + 标题栏，挂进来 = 全部历史消息的代码块换成另一套观感，同「不复用 `preview/MarkerContent`」那类静默回归）
- Zustand store 按域拆 8 slices（`store/themeSlice.ts` / `docsSlice` / `contentSlice` / `tasksSlice` / `chatSlice` / `uiSlice` / `toastSlice` / `updateSlice`），`store/index.ts` 只剩组合层 + 模块副作用，共享类型在 `store/types.ts`（`Store` 为各 slice 交集）。**action 零跨域引用**，新增域 = 新建 slice 文件，消费方 API 零改动。持久化为手写 localStorage（`mbw_*` **22 个 key**，2026-11-03 由 `grep -rhoE "mbw_[A-Za-z]+" frontend/src | sort -u` 实测 —— 此前写的 17 是旧数），无 persist 中间件。`setProcessing(key, null)` 是前缀树形删除语义（删 key 本身 + `key__` 前缀子任务），`clearDocTasks`/`cancelOneTask`/`failTask` 是兄弟函数
  - **`Toast` 支持动作按钮**（2026-11-03）：`ToastItem` 加 `actionLabel?` / `onAction?`（像回收站的「撤销」），`addToast(message, type, opts?)` —— **前 3 个参数语义不变**，第 3 个仍收 type。⚠️ 两者**成对才渲染**：只有文案 = 点不动的按钮，只有回调 = 空白按钮。⚠️ `info` 此前**没有样式分支**、会落进 `else` 渲染成红色报错，已补
  - **`TaskInfo.startedAt` + `useElapsed`**（2026-11-03）：长任务显示**已用时**而非 ETA（生成 SSE 没有百分比，模型也不知道要写多长，编一个剩余时间就是误导）。⚠️ `setProcessing` 仍是全量替换，**只对 `startedAt` 一个字段做继承**，且继承判据必须是「现有任务 `status === 'running'`」—— `failTask` 保留条目 5 秒且不清 `startedAt`，而重试按钮最可能的用法正是**失败后 5 秒内点**，只看「这个 key 有任务」会让重试一上来就显示 3 分 05 秒。两处渲染（`TaskPanel` / `ProcessingPlaceholder`）共用 [hooks/useElapsed.ts](../frontend/src/hooks/useElapsed.ts)
  - **最近打开**（2026-11-03）：[lib/recent-docs.ts](../frontend/src/lib/recent-docs.ts)（`mbw_recentDocs`，上限 12），在 `docsSlice::selectDoc` 里与写 `mbw_lastDocId` 同一处调用。**标题也存一份**（文档可能已被删，id 查不到时用户仍想知道「昨天看的是什么」；查不到传空串，保留旧标题）。启动恢复路径也写入、不跳过（那篇本来就在第一位，去重后顺序不变、仅 `at` 刷新，幂等）
- **Slot 插槽注册表**（`lib/slotRegistry.ts`）：`SlotDef`（位置 left/right/center + **`component` 组件本体（必填）** + 宽度上下限 / closable / visibleKey）注册即得可拖拽面板 + 活动栏图标，`SlotLayout.tsx` 是布局引擎（50–600px，<80px 折叠为 PanelStrip，`uiSlice.slots` 驱动宽度）。⚠️ **加一个面板 = 只改注册表一处**——组件本体自 2026-10-10 起也在 `SlotDef` 里（此前硬编码在 `SlotLayout` 的 `COMPONENTS` 映射，加面板要改两处）。⚠️ 注册表**持有页面组件**，故 `store/` 侧（`uiSlice`）**不得 import 它**（会成环）；开合判据走零依赖的 `lib/panelToggle.ts`。与 AI 浮窗（FloatingAI 覆盖式唯一形态）正交——插槽管内容面板，浮窗管对话
- **对话独立 chatStore**（`store/chatStore.ts`，2026-08-04）：对话状态**不并入主 store**——流式 delta 高频更新隔离（主 store 订阅者不受波及）。chatSlice 只留 generating；AI 浮窗状态（aiVisible/aiPos/aiSize）在 uiSlice。对话组件树：`ChatCore`（容器，内部栏受控拖拽调宽）→ `ChatSidebar`（会话列表/归档折叠）/ `MessageList`（分页窗口）/ `MessageItem`（气泡/打字机/操作栏/工具气泡/思考折叠）/ `ChatInput`（textarea/IME/@引用/token 进度）。消息模型 ChatMsg 在 chatStore.ts 定义，**勿与主 store types.ts 的 ChatMessage 混淆**


---

## 10. 主题轴、语义标记与内容块

- **GenPanel 类型门控**：5 类型选择器（网页|视频|文本|文件|图片），文件 5 子标签（PDF|Word|PPT|Excel|EPUB），每种类型只看到相关选项
- **PreviewArea 按 sourceType 分派 tab**：视频 3tab / 文档 3tab / Excel 3tab / 图片 5tab / 网页 3tab / 文本 3tab + 思维导图
- **Transcript 视图分派**：JSON+segments → 时间轴（视频）；否则 → 完整 Markdown 渲染
- **处理进度按 tab 粒度**：生成笔记只影响 summary/polished tab，图片处理只影响对应 mode tab
- **主题系统**：六轴独立 — `data-theme`(明暗) × `data-accent`(26套配色) × `data-base`(14种基调) × `data-texture`(6种纹理) × `data-font`(5种字体) × `--texture-opacity` 强度滑条。CSS 变量（`--tk-*` 全动态）+ Tailwind v4 `@theme` 引用。一键预设包（20个: 护眼/经典/粉色/雅致/国风各4）。Markdown 用 `--md-*` 变量（全部引用 `--tk-*` 或 HSL 派生，零硬编码色值）。⚠️ **数据层已抽到 [styles/theme-layer.css](../frontend/src/styles/theme-layer.css)**（2026-10-16），前端与**后端导出读同一份**
- **主题模块**：`lib/theme-axes.ts`（**轴注册表**，加一条选择型轴 = 改 2 处）+ `lib/accent.ts` / `lib/texture.ts` / `lib/theme-base.ts` / `lib/theme-presets.ts` / `lib/theme.ts` — 各轴独立管理，Store 统一分发。⚠️ **这几个模块只存 key 与 label，一个色值都不存**（色块走 `data-*` + `var()`，值与实机同源），`ACCENT_SWATCH` / `TEXTURE_SWATCH` / `BASE_OPTIONS` 的色值字段已删、别再建
- **骨架轴（2026-10-10；2026-10-11 判据改由标记决定）**：`lib/card-prefs.ts`（localStorage `mbw_card`）→ `data-skeleton="card"`（**被 `card` 节修饰符标过的章节成卡**，其余保持普通正文）+ `data-card-style`（8 套视觉方案，[styles/cards.css](../frontend/src/styles/cards.css)）。⚠️ **不再是「每个 H2 一张卡」** —— 用户 2026-10-11 判「章节卡片太简单粗暴，卡片应该用在需要强调的地方」，故改由模型在 `## 标题` 的**下一行**写 `<!-- limo-block:v1 type="card" -->` 决定哪几节成卡（判据 `Section.card`，前后端同源）。于是卡片在页面里是**混排**的、**没有外层容器**（`.md-cards` flex 已删——套容器会把**普通章节的每一行**变成独立 flex item），间距由 `.md-card { margin-block }` 承担。⚠️ **不是第七轴** —— 六轴描述「界面长什么样」，它描述「正文怎么组织」；放 themeSlice 只因与 texture/font 同属「影响预览观感的外观设置」，**刻意不进 `THEME_PRESETS`**（换主题包不该连带重置骨架）。⚠️ **`paper`（默认）时两个属性都 `removeAttribute`** —— 零回归靠「**选择器不匹配**」（`cards.css` 每条规则带 `[data-skeleton="card"]` 前缀）而非「取值相同」。⚠️ 切分器单一真相源 `lib/markdown-scan.ts::scanHeadingLines`（`extractHeadings` 已改为消费它，**围栏内 `## ` 不算标题**）；`lib/markdown-sections.ts::splitByH2` 切分；`CardSections.tsx` **每张卡各跑一个现有 `MarkdownPreview`**（绝不新起第二个渲染器）；接入点**只有** [PreviewArea.tsx](../frontend/src/components/preview/PreviewArea.tsx) 的 notes/polished 只读态（**刻意不在 `MarkdownPreview` 内部按骨架轴分派** —— 会静默扩散到 Excel 数据摘要 / 原始转录两处）。⚠️ 设置页给 8 套方案各画了一个**迷你视觉小样**（`.card-preview`，消费与 `.md-card` **完全相同**的 `--card-*` 变量 ⇒ 无第二份真相源；圆角/内框按 0.5 缩放是缩放**同一个变量**）—— 依据是 `[data-card-style="x"]` 定义变量的那批规则**不带**骨架前缀，故小样元素自己写个属性就能取到。⚠️ **逐维度微调（2026-10-11）**：`bg` / `lift` / `border` 三项，`null` = **跟随方案（默认）—— DOM 上一个 inline 变量都不写**，故新增旋钮不改变既有用户观感；写的是 `<html>` 上的 **inline 变量**（`--card-bg-base` / `--card-lift` / `--card-border`），特异性天然压过方案块。三处硬约束：① **切方案时清空微调**（微调是「方案之上的一层覆盖」，换地基就该重新决定）；② **透明基色下抬升强制归零**（`color-mix` 的 `transparent` = `rgb(0 0 0/0)`，抬升混出的是半透明**黑**，是无效组合）且 `applyCard` **第一步无条件清空 inline**（`isLiftDisabled` 要读运行时 `--card-bg-base`，不清空会读到自证循环）；③ **受控变量清单只有 `CARD_VARS` 一份**（`applyCard` 清空、`serializeCardVars` 拼导出串都从它派生）。⚠️ 微调**只作用于章节卡片** —— `blocks.css` 的十二种语义块不吃 `--card-*`
- **折叠阅读（2026-11-08）**：预览区工具栏三档 `topic` / `lead` / `full`（`lib/fold-prefs.ts`，`mbw_fold`，**默认 `full` = 改造前现状**），`FoldView.tsx` 渲染前两档，`lib/fold-scan.ts` 供数据。**档位优先于骨架轴**：`topic` 自己挂 `data-skeleton="card"`，不看用户选的 paper/card；`full` 逐字走原分派。⚠️ **折叠态不把 Markdown 交给渲染器**：一节缩成一句再喂 `MarkdownPreview`，表格会散成一串竖线、围栏不成对、单行 `![...](...)` 凭空成图、`limo-block` 标记被 `rehypeStripComments` 剥掉 —— 全都零报错。所以未展开只有 `h2`（React 直接画，带 id 供 TOC）+ `leadOf` 产出的**纯文本**一句；展开的那一节交 **`MarkerContent`**（不是 `MarkdownPreview`），语义块外壳与全文页逐字一致。⚠️ **卡片不能是空的**：`hasBody` 由 `hasRealContent` 判（标题行 / 标记行 / 注释 / 分隔线都不算内容）—— 拿 `body.trim()` 判永远为真，「只有 `# 一级标题` 的引言」会渲染成一张只有展开箭头的空卡。引言里的第一个标题抽成卡片标题（`.fold-preamble`，整行、字号大一号），只剩注释/标记行的引言才不渲染；「有内容、抽不出句子、类型也认不出」的节兜底一行「展开查看」。⚠️ **`FoldSection.body` 不含标题行**：标题行（`.fold-head`）展开时并不消失，body 再带一条 `## 标题` 会让同一张卡出现两条一样的标题。⚠️ **两种「内容很少的节」各有专门形态**：只有标题的节（`hasBody=false`）在主题档做成**整行分隔条**（`.fold-hollow`，占一个格子会变成一张空卡）；正文只有一句**纯文本**的节（`expandable=false`）**不可展开、不给标识** —— 展开前后逐字一样；整节是图片 / 表格 / 公式时 `leadOf` 抽不出句子，改用**类型说明**（`hint`，如「含表格、公式」）充当那一行，否则卡片上只剩一个标题。`expandable` 的判据是「展开后会不会多出信息」（多行 / 围栏 / 块标记 / 行内记号 / 被截断 → 是），宁可多给点击，不可少给：折叠态是纯文本，漏判一处就等于那些链接与公式永远看不到。⚠️ 切节复用 `splitByH2` / `scanHeadingLines`，`headingId` 来自 `lib/markdown-heading.ts`（渲染侧 / TOC 侧 / 折叠侧三处同源，另算一份会让目录点击静默落空）。⚠️ **档位持久化，消费方必须自己回落**：`PreviewArea.tsx::foldActive` 在「非 notes/polished tab / 没有 H2 / 流式 / 编辑态」时强制 `full`。⚠️ **入口不按字数**：字数判据住在提示词侧（哪些注入只对长文加），与「按钮要不要出现」无关；混在一起会让同样能分节的短文没有入口，而界面上没有任何解释。**2026-11-13 加「全部展开 / 全部收起」**：展开状态仍住在 `FoldView`（只有它知道哪些节 `hasBody && expandable`），动作经 `useImperativeHandle` 暴露成 `FoldViewHandle`，`{expandable, expanded}` 回传给 `PreviewArea` 决定按钮文案 —— 父组件另存一份 `open` 会与手动点开的状态分叉。按钮挂 `ContentBanner.foldActions`，位置在原链接**左侧**（阅读动作在左、跳出动作在右）；`foldActive === 'full'` 时不渲染。⚠️ 尺寸接在根容器的 `font-size: var(--read-font-size)` 上、内部全 `em`（网格 `minmax(max(15em, 200px), 1fr)`），不接就不跟 `Ctrl+滚轮`，且不报错。`styles/fold.css` **不进导出**（同 `ArtifactMetaBar`）
- **语义标记 `limo-block`（2026-10-10）**：产物正文里的**协议元素** —— 模型在笔记里写 `<!-- limo-block:v1 type="key-takeaway" -->` … `<!-- /limo-block -->`（块内是普通 Markdown），渲染成**轻量强调块**（左边框 + 淡底，**刻意不是卡片** ⇒ 与骨架轴正交、无「卡中卡」）。⚠️ **十二种块，分两族**（2026-10-11 由 1 种 → 4 种 → 6 种 → 9 种 → 2026-10-20 补 `cycle`、`sequence`、`quadrant`）+ ⚠️ **`card` 是节修饰符不是块**（见本节末）：
  - **九个语义强调块**（回答「这段文字是什么性质」，块内原本就是散文）：`key-takeaway` 重点 / `alert` 提醒·异常 / `recommend` 推荐 / `metric` 指标 / `steps` 横向步骤条 / `timeline` 竖向时间线 / `cycle` 环形 / `sequence` 交互时序 / `quadrant` 二维评估（后三种 2026-10-20）；
  - **三个内容卡片**（2026-10-11 补，**性质不同族**）：`table` 对照表 / `code` 示例代码 / `image` 单独展示的图 —— 它们标的是**内容本身**（表格 / 代码 / 图片本来就与正文不同质），加标记的**全部作用**是外面多一层壳、**块内一个字节都不动**。故 CSS 里这一族**不换 `display`、不换子元素**，只做中性容器装饰（**不带语义色** —— 染色等于替它加一层它没有的意思）。⚠️ **刻意不是「把表格/代码改造成卡片形状」**：图鉴明确禁止「为了统一视觉而把代码改成普通文字卡片」，保住等宽/缩进/复制/横向滚动是前提。⚠️ **卡片外壳的选择器只能落在 `.md-block` 自己或块内通用元素（`table`/`pre`/`img`）上** —— 预览侧的 `.md-table-wrap`、代码块工具条 `.group` 在导出侧**根本不存在**（导出是裸 `<table>` / `codehilite`），碰它们就当场分叉（这层预览/导出结构差异**先于本轮就存在**）。
  - **`card` 节修饰符**（2026-10-11 补，**第三种性质**）：⚠️ **它不是块** —— 其余十一种标的是**一段内容**（包裹式、有闭合标记），`card` 标的是**一个 `##` 标题及其全部后续内容**，写法是「写在标题**下面一行**」、**没有闭合标记**。它是前端骨架轴 `card` 的**唯一触发源**（没有勾选项 ⇒ 模型不写它就等于这个轴什么都不做，不报错不警告）。⚠️ **定性成节修饰符是一个决定解决两个隐患**：① 模型的本能是把标记写在标题**之前**（其余十一种都是包裹式），当块处理会让行分类器进入块内状态、把那个 `## 标题` 整个跳过 —— **整节从文档结构里消失**；② 节内第一个真块的闭合标记会被当成 `card` 的闭合。现在 `match_open` 对它返回 `None`（`is_section_card` 是**另一个**判据，有对照实验钉住两者方向恰好相反），`parse_blocks` 里紧跟的标记行剥除顺手接住它 —— **零残留降级无需新分支**。⚠️ **`strip_break` 挂在 `card` 上而不是挂在骨架轴上**：成卡的节剥尾部 `---`（卡的下边界已承担分隔），**不成卡的节保留**（它没有卡边界，`---` 是唯一的分隔手段）；漏了后者就是「开了卡片模式、普通章节之间的分隔线却消失了」。⚠️ **它是唯一没有集合守卫盯着的 type**（刻意不进 `ARTIFACT_BLOCK_TYPES`），名字在场 + 「写在标题之下」+「没有闭合标记」三条只能由 `test_block_prompt.py::test_规范教了节级修饰符_card` 手写钉住。⚠️ **2026-10-11 实机修正：它在规范里被提到最前面**（`PROMPT_VERSION` `"12"` → `"13"`）—— 原先是列表第 10 项、示例还单独放在整段末尾，实测结果是**其余九种几乎全写对、独独 0 个 `card` 标记**（v12 只跑过 1 篇，样本小但现象干净）。推论是**形态差异**造成的：九个块级标记是同一形态的反复（只有 `type` 值不同），模型扫一眼就抓住了模式；`card` 是唯一形态不同的那一个（不包裹内容、写在标题之下、没有闭合标记），吊在长列表末尾最容易被当成「又一个 type」略过。故按**层级**重排：先讲节级的 `card`（粒度最大）、示例紧跟其说明，再讲段级九种。⚠️ **判据与类型一字未改** —— 改的是位置与结构，与同日「不堆劝退话术」的取向不冲突；
  ⚠️ **`steps` / `timeline` 两者性质是「迁移」不是「新增」**——它们替代图表管道里的 `hchain` / `timeline` 两种简单线性图（判据：Markdown 的列表结构能保真表达这种关系，就不必付画图的成本），故 `get_chart_format` 的布局引导**同步删掉这两种**（**两处必须同批改**，否则同篇里一个说「画图」一个说「别画」；算子仍留在 `_LAYOUTS` 里，删的是引导不是能力）。⚠️ 两者的 CSS 判据依赖**列表类型**（`steps` 用无序 → `<ul>`、`timeline` 用有序 → `<ol>`，各自靠 `li::after` 画箭头 / `li::before` 画序号圆点），故「`steps` 写列表、`timeline` 写有序列表」这条**必须写进 prompt**——模型不按约定写就退化成普通内容（零残留降级，但白标了一个块）。⚠️ **形态差异全部由 CSS 表达，一行业务 DOM 结构都不许变**——依据是 `blocks.css` 是导出侧 `_load_block_css()` 读的**单一真相源**：结构一旦分叉，Python 的 `render_blocks_html` 就得复刻 React 的结构（模式 #54），「预览所见 = 导出所得」退化成靠人工对齐维持。收益连锁：`MarkerContent.tsx` 与 `render_blocks_html` **零改动**。代价如实记录：`metric` **刻意不做并排**（并排要网格容器 = 结构变化），改用 `h3 + p` 相邻兄弟选择器做「`### 名称` 小标签 + 放大数字」。⚠️ **metric 数字 2026-10-17 起用 `--tk-accent`**（原 `--tk-success` —— 固定绿不随主题，而同文件注释本来写的就是「accent 留给 key-takeaway / metric 的数字」）+ `font-variant-numeric: tabular-nums`；同批宽表 `overflow-x: auto` + 首列 `sticky`、大图 `max-height: 70vh`。⚠️ **`overflow-x` 必须配 `flex-shrink: 0`**（2026-10-18 实机踩到）：`.preview-content` 是 `flex flex-col overflow-y-scroll`，overflow 非 visible 的 flex item 自动最小尺寸按 **0** 算 ⇒ 表格块被压成「很小的一行」+ 内部滚动条，内容完整、零报错。**同一 bug 还有第二形态、更严重**：关掉「内容块装饰」时归零规则把 `padding`/`border` 一并去掉 ⇒ 被压缩的块连那 26px 可视高度都没了，**块高恰好 0、整块消失**（内容仍在 `scrollHeight` 里）——所以「关装饰吞卡片」不是开关的问题，缺的就是这个属性（四组对照实测见 CHANGELOG 2026-10-18）。`artifactCardShape.test.ts` 有三条守卫：table 专项、**任何 `.md-block` 本体上的非 visible overflow 都必须配它**（通用版，防将来新增块漏配套）、关装饰规则**不得**出现 `display:none`/`visibility:hidden`/`opacity:0`/`height:0`（「只收装饰绝不吞内容」）。三者均只影响预览与导出 HTML/PDF（Word 不读 CSS）；⚠️ **`overflow-x` 管不到打印/PDF**（打印媒体没有滚动机制，长宽表右侧列仍会被裁切，见 CHANGELOG 2026-10-18「已知偏差」）。⚠️ `alert` 用 `--tk-warning` **不是** `--tk-error`（严重度由**文字**承担，颜色不该替它分级）；导出侧 `_CARD_TOKENS_CSS` 必须同步补令牌（成对常量守卫）。⚠️ **中性面板族（`metric` / `table` / `code` / `image`）的容器边框用 `--tk-border`、不是 light 档**（2026-10-11 实机修正）：`--tk-border-light` 在默认 `warm` 基调下离正文底只差 9 个灰阶、面板底色 `--tk-elevated` 离正文只差 6 阶 —— 三层叠加等于**没有容器**（用户原话「显示效果不太好」）；`--tk-border` 离正文 30 阶（3.3 倍），且与章节卡片默认方案 `gpt` 是**同一套语言**（两者本是嵌套关系：卡片里包面板）。⚠️ `timeline` 的**竖连线仍用 light 档**（细线本就该淡），故 `markerPrefs.test.ts` 的新守卫限定在两条**容器**规则内 —— 写成全局断言会把连线一起误判。解析链 `lib/markdown-scan.ts`（**唯一的行分类器**，`inFence` + `inBlock`）→ `lib/markdown-markers.ts`（`parseMarkers` / `stripMarkers`）→ `preview/MarkerContent.tsx`（每段各跑一个现有 `MarkdownPreview`）。⚠️ **接入点只有两处**（PreviewArea 的 paper 分支 + CardSections 每张卡的 body），**绝不放进 `MarkdownPreview` 内部**（会扩散到 Excel 数据摘要 / 原始转录）。⚠️ **顺序是 Markdown 层的：先切卡、再在每节内解析标记**（跨节标记在降级里自然消解）。⚠️ **开关语义是「隐藏装饰」不是「隐藏标记」，且射程是全部十二种**：`lib/marker-prefs.ts`（`mbw_markers`，默认 `decor: true`，UI 名「**内容块装饰**」——2026-10-11 由「重点块装饰」改准）关掉后**内容一字不少** —— 九个强调块只是边框与淡底消失，**三个内容卡片则退回「没包过围栏」的样子**（裸 `<table>` / 带工具条的代码块 / 靠左的图；间距刻意保留成与预览侧 `.md-table-wrap` 的 `my-4` 同值，故「关掉装饰」与「从未包过」逐字相同）。**落点是纯 CSS 层**（`[data-marker-decor="off"]` 前缀规则），**不是 JS 分支不渲染包裹层**（那会让后端导出侧再实现一遍）；DOM 落点 `data-marker-decor="off"`（**默认不写属性**⇒ 零回归靠「选择器不匹配」）。⚠️ **新增 type 时必须同步补它的 `decor=off` 归零规则**——漏了不报错，只是那一种块永远带着装饰。⚠️ 后端协议收口 `core/artifact_blocks.py`（与 `transcript_meta.py` 并列：一个管自证头、一个管正文标记），**前后端各一份实现靠共享 fixture `limo-blocks.json` 钉住**。⚠️ **prompt 无条件注入且无勾选项**（价值全在「模型自主判断重点」，先勾选就退回旧问题）—— 故「谁该有谁不该有」只能靠测试钉住
  ⚠️ **`cycle`（2026-10-20）是同一判据的延伸，但替代的**不是**一张图**：图表管道从来没有环形算子（`_LAYOUTS` 十个里没有），`flowchart` 遇环直接抛错、`state` 只适合状态迁移图 —— 模型想表达「A→B→C→A」时只能硬套 `state`，而 `state` 曾经写死 `nrow > 7` 就抛错（实测失败产物里 2 条正是这个；那条闸门已于 2026-10-20 随同批的 `flowchart` 闸门一起删除，理由见上方图表段 —— 真正的裁决者是 `expand()` 的缩放链）。`cycle` 把它变成**填空**：模型只写「有哪几个环节、顺序如何」（3–6 项**有序列表**），环形布局由 `blocks.css` 算，坐标 / 半径 / 连线完全不经模型之手。⚠️ **角度档靠 `li:first-child:nth-last-child(N)` 判项数，刻意不用 `:has()`**（本文件同时被导出 HTML 消费，为一条可优雅降级（退回普通列表）的装饰去赌目标浏览器版本不值得）；⚠️ 项数不匹配或超出 3–6 时全部节点叠在环顶 —— 不好看但**内容一字不少**（「内容永远不该因为渲染不认识而消失」优先于观感）；⚠️ **`overflow-x: auto` + `flex-shrink: 0` 成对**（与 `table` 同款，窄面板宁可横向滚动也不让节点叠成一团）；⚠️ 关装饰退回**有序列表**（与 `timeline` 同出口）；⚠️ `get_chart_format` 同步把「首尾相接的因果循环」从图表职责里分出去，`state` 的示例标题改准为「状态机（带判定的迁移，可含回环）」，避免与它抢同一批内容（**两处必须同批改**，同 `steps`/`timeline` 的分家纪律）
  ⚠️ **`sequence` / `quadrant`（2026-10-20）同样是「迁移」不是「新增」**——它们接管 `get_chart_format` 同日撤下的两种布局（图表引导由七种收缩到三种，判据见上方图表段）。两者的**形态前提必须写进 prompt**：`sequence` 用**有序列表**、每项以 `**发送方 → 接收方**：` 起头（⚠️ 纯 CSS 无法从消息文本推导参与者列位，故它只能是「消息流卡片」而不是生命线时序图 —— **方向由文字承担**，这正是它不必结构分派的原因）；`quadrant` **恰好 4 项**、顺序固定「左上 / 右上 / 左下 / 右下」（与 `chart_layout` 的 `quadrant` 算子同序），CSS 用 `grid-template-columns: repeat(2, minmax(0, 1fr))` 排 2×2 且**不画坐标轴**（象限名自己承担语义）。项数不对时退化成两列多行 / 普通列表，**内容一字不少**；两者都有 `decor=off` 归零规则。⚠️ **判据是「文本结构能否保真表达这个关系」**，不是复杂度、也不是垂直/水平 —— `layers`/`tree` 因此干脆连块都不必标（嵌套列表本身就是那个结构）
- **语义标记 · 对话侧（2026-10-11）**：同一套协议、**另一套类型**（两侧集合**不相交**，由三集合 `ARTIFACT_BLOCK_TYPES` / `CHAT_BLOCK_TYPES` / `KNOWN_BLOCK_TYPES = 并集` 拆开——「协议知道」≠「某侧实现了」，混为一谈的失效是静默的）——`card-group`（并列比较 2–4 个同级对象 → 块内 `###` 各成一张**并排卡片**）/ `follow-up`（2–4 个下一步问题 → 每项一颗 chip，点了**直接发送**）/ `info`（信息卡，「XX 是什么」）/ `digest`（摘要卡，长回答开头的整体印象）——后两种 2026-10-11 补；2026-10-12 再补 `caution`（注意事项，色用 `--tk-warning` **不是** `--tk-error`，同产物侧 `alert` 的判据）/ `how-to`（操作步骤，块内是**有序**列表，CSS 靠 `<ol>` 认）/ `quote`（引用出处）→ **共七种**，规范含**总量闸门**（这些块合计不超两块）。⚠️ **新类型名刻意避开产物侧十二种**（`caution` 而非语义相近的 `alert`、`how-to` 而非 `steps`）—— 名字撞上等于「同一套渲染器服务两种语义」，而两者的降级判据 / CSS 归宿 / 导出待遇都不同。⚠️ 对话侧**不受**「只许 CSS」那条约束（`chat-blocks.css` 明确不进导出），故可用 React 结构与 lucide 图标。⚠️ **只共享判据、不共享渲染器**：`ai-chat/ChatMarkerContent.tsx` 是**新组件**（内联 `ChatText` = 原来的裸 `ReactMarkdown`），**刻意不复用** `preview/MarkerContent`（它内部硬编码 `MarkdownPreview` → 复用等于把整个对话正文换成产物渲染路径，波及**全部历史消息**）；共享的是 `parseMarkers` 与新增的 `splitByHeading(md, level)`（`splitByH2` 改为委托，行为逐字不变）。⚠️ **流式半截标记行必须抑制**（`markdown-scan.ts::suppressTrailingPartialMarker`，**只在对话正文且 `streaming` 时调用**）：`<!-- limo-block:v1 type="card-` 这种半截行既不匹配开标记也不匹配 `MARKER_LINE_RE` → 会被当普通正文渲染出来；⚠️ **判据必须命名空间感知**（朴素的「以 `<!--` 开头且无 `-->`」会吞掉**正在输入的合法 HTML 注释**，测试有对照实验）。⚠️ **消费点**：存储/LLM 历史**保留原文**、`.md` 导出与复制**剥离**、`.json` 导出**刻意保留**（可回灌）、未闭合/未知 type **零残留降级**。⚠️ **视觉跟随卡片方案（2026-10-12）**：容器的 `border` / `border-radius` / `background` / `box-shadow` 消费 `--card-*`（与产物侧章节卡片**同一套变量、同一套 8 方案**），**尺寸级（padding / gap / margin）保持 em** —— `--card-padding` 是 `22px 26px` 的**宽栏**值，浮窗里会撑爆，而对话间距必须跟 `lib/chat-font.ts` 的字号倍率一起缩。**零 JS 改动、零中间变量层**：`--card-*` 的初值写在 `cards.css` 的**裸 `:root`**、`paper`（默认骨架）下 `applyCard()` 只 `removeAttribute` 两个 data 属性**不删变量** ⇒ 对话侧直接 `var()` 即可，默认拿初值、用户切方案自动跟随，**不需要知道当前是哪套**。⚠️ **`--card-border` 在 `apple` / `ink` 两套下是 `none`** —— 语义差异此时由**图标色**承担。⚠️ **两个交互（2026-10-12）**：① **卡片整体复制** —— `ChatCopyButton` 泛化，落点克制（`card-group` 每张卡 + `caution` / `how-to` / `quote` 外壳；`info` / `digest` **刻意不加**，内容短、复制价值低于它占的注意力，有**反向断言**守着防「顺手补齐」）；② **追问换一批** —— `POST /api/chat/followups`（**无状态**：`{question, answer, model_id}`，不碰 `SessionManager`）+ `ChatFollowUpList.onRefresh`（**缺省不渲染按钮** ⇒ 产物侧复用零回归）+ `chatStore.refreshFollowUps`（成功替换 + 落库；**失败保留原有那批** + 记 `followUpError`）。⚠️ **端点无状态的根因**：前后端消息 id **不同源**（前端 `nextMsgId()` vs 服务端 `msg_…`，会话进行中必然不同），改用**下标**则多一层「前端数组与服务端数组同步」的隐含前提 —— 而 `editMessage` / `deleteMessage` / `regenerate` 三个写动作都在改它。代价是落库由前端承担（与那三个写动作同款）。⚠️ **`.markdown-chat` 基础元素（表格 / 代码 / 引用 / 列表）2026-10-12 从 `index.css` 搬进 `chat-blocks.css`** —— 它与语义块作用在**同一批节点**上（块内就是普通 Markdown），分住两处时「给块内的表格加样式」得先想清楚谁压谁。⚠️ **代码块与表格加壳走纯 CSS、刻意不走 `limo-block`**：加壳没有任何需要模型判断的东西，标记驱动只会多出一个「模型漏标 = 静默失效」的失败模式，且纯 CSS 让**全部历史消息立刻受益**（与产物侧**标记驱动**的 `table` / `code` 差异是有意的 —— 那边要保证「预览 = 导出所得」，这边**不导出**）。⚠️ **对话样式独立**（`styles/chat-blocks.css`，`.chat-*` 不共用 `.md-block`、尺寸用 em、零硬编码色值、**不进导出**）。⚠️ prompt 是 `agent/prompts/formats.md`（system prompt 第四层，无条件注入）；**两侧规范刻意不共用一份文本**（有「不串味」断言）。⚠️ **对话侧形态判据（2026-10-17/18）**：`.chat-block` 用**四边框** `1px solid var(--block-edge)` 且**自持、不跟随 `--card-border`**（`apple`/`ink` 两套把它设为 `none`，跟随会让语义块只剩图标悬空；圆角/底色/投影仍跟随）；`.chat-cards` 列数**按张数**（1/2/3/2×2/5+ 单列，`@container (max-width:320px)` 兜底），**不再按宽度**；篇幅变体（4+ 内容块 → 长内容卡、7+ 列表项 → 整组退列等）全是纯 `:has()` 结构判据。⚠️ **长内容卡 / 退列的 `:nth-child(5)` 数的是 DOM 子节点，第一个恒为复制按钮** —— 按钮**流式期间隐藏但不卸载**（`.chat-card-copy--streaming` + `display:none`），卸载会让同一判据在两态数出不同的数、卡片在流式结束那一帧跳变且零报错；`chatCardShape.test.ts` 有**读组件源码**的守卫钉住「ChatCard 函数体内不得出现 `!streaming`」
- **⚠️ github-markdown-light.css 覆盖**：`main.tsx` 导入的第三方 CSS 硬编码 `.markdown-body { background-color: #ffffff }` 和 `#f6f8fa` 等冷灰色。`index.css` 中用 `!important` + `--tk-*` 变量覆盖。新增 markdown 背景样式时必须同步检查此文件。提交前 `grep` 确认零硬编码色值泄露

### 导出系统

> **正文见 [export-pipeline.md](export-pipeline.md)**（946 行，2026-11-07 起本文件只留规则）。
> 那里有：两条路径的完整设计 / Tab→内容映射表 / `markdown_to_html` / Pygments / KaTeX /
> 四种 HTML 主题与「跟随应用主题」六轴 / Word 的两跳链路 / PDF 的打印 CSS 三处落点与
> WebView2 陷阱 / 证据块线性化的四个接入点 / 卡片骨架跟随。**改导出先读它**。

下面五条是**跨模块的硬约束**，写别处的代码时也会违反，所以留在本文件：

- **预览所见 = 导出所得。** 导出与预览不一致就是 bug，不区分「内容」还是「样式」。
- **正文里的 HTML 注释一律不进渲染**（前端 `lib/markdown-plugins.ts::rehypeStripComments`，
  一处覆盖全部 7 个渲染点；后端 `docx_export.py` 剥 bs4 的 `Comment` 节点 —— 它是
  `NavigableString` 子类，不剥会当正文段落写进 Word）。⚠️ 两处例外：`.md` / `.html` 导出
  **刻意保留**自证头（`_pick_artifact` 用 `raw_content`）；插件必须**跳过 `code` / `pre` 子树**
  （代码块里的注释是 `text` 节点，但那是用户要展示的代码）。
- **`limo-block` 在 HTML/Word/PDF 上是「转换」、在 md/纯文本上是「剥离」** —— 前者没有视觉层
  可谈，后者要跟随预览（`marker_decor` 开关）。⚠️ 无标记产物必须**逐字节零回归**。
  ⚠️ 证据块是唯一例外：四种格式（**含 Markdown**）统一线性化成 `[证据 N]` + 文末附录。
- **新增一个 tab = 注册一个 `TabDef` + 一个 `CONTENT_GETTERS` getter**（`lib/tabRegistry.ts` /
  `lib/contentSources.ts`），不改分发逻辑。⚠️ `contentSource === 'none'` 的 tab 在导出面板里
  必须被滤掉，后端对不适用的 tab 返回**空串、不回退 rawText**。
- **导出走 `apiUrl()` / `downloadFromUrl()`，调用方传路径时禁止加 `/api` 前缀**（它们内部已拼
  BASE），否则 `/api/api/...` 404。这条对**所有**前端请求成立，不只导出。

⚠️ 新增导出格式、改打印样式、改卡片导出之前，先读 [export-pipeline.md](export-pipeline.md)
的 **§「新增导出格式」** —— 那里有一次格式扩展的完整落点清单，漏一个的表现是
「某个入口静默不跟随」。尤其记住 `export_custom` 与 `_build_export_zip` **各有一份
`for fmt in formats` 循环**，新增格式必须两处都加。

### 安全
- **敏感信息脱敏**：LLM Key / Cookie / Tavily Key 在 `GET /api/config` 中统一脱敏返回（`mask_sensitive`），保存时 `merge_sensitive` 防 `***` 覆盖真实值
- **SSRF 防护**：`url_security.validate_url()` — 内网/保留 IP 段黑名单（**v4 + v6 双栈**，`::ffff:a.b.c.d` 按内嵌 v4 判）+ DNS 解析二次校验 + 协议白名单。⚠️ WebpageAdapter 曾遗漏（已修复），新增管道必须加。⚠️ **重定向必须逐跳校验**：`safe_get` / `safe_get_httpx`（`allow_redirects=False` + 每跳 `validate_url`），`requests` 默认自动跟随会让公网 URL 一跳 302 到 `127.0.0.1`
- **导出 HTML 的 CSP + nonce**（2026-10-20）：`export.py::_csp_meta` 注入 `script-src 'nonce-<每次随机>'`，我们自己的 `<script>` 全带 nonce，**正文里的 `<script>` / `on*=` 一律不执行**。⚠️ 为什么必须：导出 HTML 是「模型/用户内容原样渲染」，而 Tauri 的 PDF 路径在**同源**上下文里导航到它 —— 一段脚本就能读全库、调写接口。⚠️ **HTML 内容一个字都不改**（保「预览所见 = 导出所得」），`style-src 'unsafe-inline'` 必需
- **有副作用的 GET 的来源守卫**（2026-10-20）：`core/origin.py`，`X-LiMo-Client` 头（主判据 —— 跨站 `<img>`/`<script>` 是 no-cors、带不了自定义头）+ Origin/Referer 白名单兜底。CORS 只拦跨站**读响应**，不拦请求发出。⚠️ 白名单正则与 `main.py` 的 CORS **同一份**。**2026-10-27 起覆盖 `/api/plugins/*` 六个写端点**（install / enable / disable / delete / install-deps / config —— 此前一个都没过，而 install 的载荷是「重启后 `exec_module` 执行的任意 `.py`」；`GET /plugins` 只读，刻意不加）。⚠️ **新增任何写端点先看这里**
- **日志值必须截断（含容器）**：`core/logging.py::_val` 字符串 80 / 容器 300。⚠️ 容器曾经不截断 ⇒ `log.info("Tool", args={...})` 把整段工具入参打进环形缓冲，`GET /api/logs` 交给前端 LogPanel，用户贴日志时顺带外泄会话内容
- **路径遍历防护**：`_safe_filename()` 取文件名末段 + `resolve().relative_to()` 双重校验，图片/文件服务均防护
- **Markdown XSS 防护**：前端 `<a>` 组件拒绝 `javascript:`/`data:` 协议，ReactMarkdown 默认不渲染原始 HTML
- **SSL 证书 (PyInstaller)**：启动时从 `certifi.where()` 设 `SSL_CERT_FILE` + `REQUESTS_CA_BUNDLE` 环境变量。缺此则 dashscope SDK / openai / yt-dlp 的 HTTPS 请求全部失败
- **代理注入**：启动时从 `config.fetch.proxy` 读到代理地址后设 `HTTP_PROXY`/`HTTPS_PROXY` 环境变量，确保所有 HTTP 库（dashscope/requests/httpx/yt-dlp）统一走代理

### 其他
- **数学公式**：全局 `remark-math` + `rehype-katex`，LaTeX `$$` 块自动排版
- **日志系统**：`contextvars.ContextVar` 注入 `request_id` → ASGI middleware，全链路追踪。`MemoryHandler`（`main.py` 中配置）缓存最近 1000 条日志供前端 LogPanel 轮询（`GET /api/logs`），重启后清空
- **性能打点**（2026-10-27）：`GET /api/perf`（**app 级端点**，不在 `@router.` 的 99 个口径里）→ `perf.snapshot()`，按 p95 降序给各分组的 `count/min/p50/p95/max/avg`；数据双轨 —— 进程内分位数（每组 500 样本 / 组数上限 200，重启清零）+ 全量留档 `{data_dir}/perf.log`（JSON Lines，按天滚动保留 14 天）。**入口是日志面板的「性能」标签页**（`components/ui/LogPanel.tsx`，一键复制为 Markdown），⚠️ 表格**刻意不做颜色分级**。⚠️ `perf.log` 要**重启后端**才新建（`delay=True`，没有第一次写入就不建文件）。详见「公共常量的家」表的 perf 行
- **客户端断连监测**：`app/core/disconnect.py` — `@asynccontextmanager` 封装，5 个 SSE 端点复用，新增端点直接 `async with disconnect_watcher(request, cancel_event):`
- **Tesseract 移除**：本地 OCR 已从内置移除（2026-08-02），`backend/bin/tesseract/` 不再随包分发。**行为变更**：无 dashscope key 时扫描件 OCR 从「本地可用」变「占位文本（原图 + _所有 OCR 引擎均失败_）」，直到用户装三方插件模板
- **EPUB 一键解读**：epub-container（自研解析器，2026-08-26 起替代 ebooklib AGPL）章节提取；生成走独立管道（`app/services/epub_pipeline.py` + `app/routers/epub.py`）——阶段 1 逐章解读落盘 → 阶段 2 归并全书总结（notes=总结 / polished=逐章），全部章处理过即触发归并（失败章跳过并在 polished 按序插失败标注），中断续跑 + 软取消（`cancel_event`），产物必定落盘。三端点 `/api/epub/generate|task|cancel/{doc_id}`，前端 GenPanel epub 专属入口三态 UI（进度条 / 继续上次解读 / 一键解读）
- **模糊帧过滤**：`frame_quality.py` — Laplacian 方差 + 熵 + 边缘密度评分，视频抽帧时自动丢弃低质量帧
- **KaTeX 离线缓存**：`_get_katex_assets()` 从 node_modules/static 加载 CSS/JS/字体，CSS 中 `url(fonts/...)` 替换为 base64 data URI（60 个字体），导出 HTML 完全自包含（零 CDN，断网可用）
- **KaTeX 服务端渲染**：`_render_formulas_katex()` 调 Node.js 脚本预渲染公式。打包环境无 Node.js → 先 `shutil.which("node")` 预检，不存则静默回退客户端渲染
- **markmap 离线化**：`markmap.ts` 不调用 `markmap-common` 的 `loadCSS`/`loadJS`（动态 CDN 加载会被 Edge WebView2 跟踪防护拦截），KaTeX CSS 在 PreviewArea 中静态 import，hljs 由 react-syntax-highlighter 内联
- **设置持久化**：`ProviderSettings.tsx` 中引擎/LLM Key 的保存不能仅靠 `onBlur`（组件卸载时不触发），需加 `useEffect` cleanup 用 ref 兜底保存
- **上下文窗口四层解析**（2026-08-04 C1）：`resolve_context_window()`（`model_resolver.py`）——用户自定义（`enabled_models.context_window`，设置页可编辑）> llm_models 预设 > provider 默认表（`core/token.py` `DEFAULT_CONTEXT`）> 保守 32k。对话预算 `budget = context_window × 0.7`（`core/chat_context.py`），历史按 token 反向裁剪 + 文档分级截断
- **路由异常标准化**：**19/19 个路由模块已全部完成 AppError 迁移**（2026-10-11 复核：全仓 `grep -l "HTTPException" backend/app/routers/*.py` 结果为空）。迁移时注意模式 #36 —— 真 404/403 必须显式传 `status_code`
- **`AppError.hint` 是「下一步该做什么」**（2026-11-03）：与 `detail`（开发者看的原始报错）/ `message`（发生了什么）**三个字段各司其职**，只有 `message` + `hint` 该被直接展示给用户。`to_dict()` **仅在有值时输出该键** —— 无条件写 `"hint": ""` 会让每个响应多一个恒空字段，「有没有 hint」也就不再是可判据的事实，且没填的错误**逐字节零回归**。⚠️ **刻意不做「错误码 → 文案」全局映射表**（新架构，且一次性补全所有错误不现实）：能做的就填一个，没填的与改造前完全一样。⚠️ **生成侧的错误必须先能到达前端**：改前 catch-all `except Exception` 把 AppError 压成 `str(e)[:200]`（`AppError.__init__` 末尾 `super().__init__(message)` ⇒ `str(e)` 只剩 message）、`GEN_NO_API_KEY` 在第一个 `yield` **之前** `raise`（客户端只看到断流）—— 现在 `generate.py` 在 catch-all **之前**有 `except AppError`，yield `{type:'error', message, code, detail, hint, recoverable}`。⚠️ 前端解析顺序是 `message` 优先、`detail` 降为补充行（改前反着来），且**必须 `typeof body.detail === 'string'`** —— FastAPI 校验失败返回的是 `{detail: [...]}` 数组
- **Content-Disposition 中文文件名**：必须用 RFC 5987 `filename*=UTF-8''` 编码，否则 Starlette 会尝试 latin-1 编码 → UnicodeEncodeError
- **日志函数签名**：`app.core.logging.warning(msg, **kwargs)` 只接受 1 个位置参数 + kwargs，不能用 `%s` 占位符多参数调用。多参数用 f-string
- **空文本去重碰撞**：空字符串 SHA256 固定为 `e3b0c44...`，两个空导入会误判为重复。TextAdapter 需检查 `text.strip()` 非空
- **前端 apiUrl() 辅助函数**：`api/core.ts` 导出 `apiUrl(path)`（Tauri 下拼接 `http://127.0.0.1:7413/api`，浏览器下保持相对路径）与 `appUrl(path)`（图片/静态资源转绝对 URL，Tauri 必需）。所有 HTTP 请求必须走 `apiUrl` 或 `apiFetch`
- **processingTasks key 粒度**：`${docId}__${kind}` — 同一文档的不同操作（retranscribe/generate/process）不互相覆盖，store 提供 `clearDocTasks(docId)` 批量清除
- **设置页 overlay**：`App.tsx` 中设置页用条件渲染 overlay 而非替换整个 render tree，避免 GenPanel 卸载丢失进行中的任务状态
- **SSE 断开保护**：`streamGenerate` 追踪 `receivedDoneOrError` 标志，连接异常断开时触发 `onError('连接意外断开')` 防止 `generating` 永不复位
- **操作互斥**：GenPanel 中 `lockOtherOps` — 转录/图片处理中禁止生成，反之亦然。`Ctrl+G` 操作进行中拦截+Toast 提示
- **取消机制**：5 个端点全部覆盖 — generate/ingest/retranscribe/chat/process 均有 `cancel_event` + `_watch_disconnect`（0.5s 检测；fact_check 已随 AI 审核移除 2026-08-16）。LLM 调用逐 chunk 检查取消信号停止 token 消耗。本地同步引擎用 `asyncio.to_thread` 放到线程池避免卡死事件循环，引擎内部逐段检查 `cancel_event.is_set()` 立即停止 CPU

