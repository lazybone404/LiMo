/**
 * 静态演示站的假后端 —— 拦截 `window.fetch`，在浏览器里回应所有 `/api/*`。
 *
 * ⚠️ **只在 `VITE_DEMO=1` 构建里被加载**（`main.tsx` 动态 import），正式包不含本模块。
 *
 * 为什么拦 `fetch` 而不是改 `apiFetch`：`/api/health`（`lib/health.ts` 的 10s 轮询）与
 * 两条 SSE（`generate` / `chat`）走的是**裸 `fetch`**，只改 `apiFetch` 覆盖不到它们 ——
 * 那两处会照样打到 `127.0.0.1` 或相对路径上，页面上表现为「后端离线」横幅常亮。
 *
 * 语义边界（刻意的，不是没做完）：
 * - **读**：全部返回演示数据。
 * - **整理类写**（改名 / 打标签 / 移动 / 删文档 / 文件夹 CRUD / 对话 CRUD）：在内存里生效，
 *   刷新页面即还原 —— 演示站要能点得动，否则一次误操作就留下坏数据，而用户没有「重置」按钮。
 * - **计费 / 长耗时动作**（导入 / 生成 / 图片处理 / 智能分类 / 合成 / EPUB / 导出 / 插件）：
 *   返回 `403 DEMO_READONLY`，前端以普通错误提示呈现。演示站没有后端，静默成功会比报错更糟。
 * - **对话**：真的回一段流式回答（含工具气泡与语义块），这是界面上最值得看的部分。
 */
import capabilitiesJson from './capabilities.json'
import {
  DEMO_DOCS, DEMO_FOLDERS, DEMO_NOTES, DEMO_POLISHED, DEMO_RAW, demoVersions,
} from './data'
import type { DocEntry, FolderEntry } from '../api/core'

const DEMO_FLAG = '__LIMO_DEMO_INSTALLED__'

/** 演示数据的可变副本 —— 整理类操作改这里，刷新即还原 */
const docs: DocEntry[] = DEMO_DOCS.map(d => ({ ...d, tags: [...d.tags] }))
const folders: FolderEntry[] = DEMO_FOLDERS.map(f => ({ ...f }))

const now = () => new Date().toISOString().replace('Z', '').slice(0, 19)

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function notFound(message: string): Response {
  return json({ code: 'DEMO_NOT_FOUND', message, recoverable: false }, 404)
}

/** 演示站不支持的动作 —— 明确说清楚，不让用户以为是自己操作错了 */
function readonly(what: string): Response {
  return json({
    code: 'DEMO_READONLY',
    message: `${what}需要后端服务，这是静态演示页，数据是预置的示例内容`,
    recoverable: false,
  }, 403)
}

const sleep = (ms: number) => new Promise(r => setTimeout(r, ms))

/** 把回答切成小段逐个推送，模拟流式打字效果 */
function sseText(events: Record<string, unknown>[], delayMs = 22): Response {
  const encoder = new TextEncoder()
  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      try {
        for (const ev of events) {
          controller.enqueue(encoder.encode(`data: ${JSON.stringify(ev)}\n\n`))
          await sleep(delayMs)
        }
      } catch {
        // 用户中途取消（AbortSignal）会让 enqueue 抛错，静默收尾即可
      } finally {
        try { controller.close() } catch { /* already closed */ }
      }
    },
  })
  return new Response(stream, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
  })
}

// ── 对话演示数据 ──

interface DemoConversation {
  id: string; title: string; model_id: number | null
  created_at: string; updated_at: string
  messages: {
    id: string; role: string; content: string; timestamp: string
    doc_refs?: { docId: string; title: string }[]
    tool_calls?: { tool: string; display: string; summary?: string }[]
    thinking?: string
    follow_ups?: string[]
  }[]
  doc_ids: string[]
}

const conversations: DemoConversation[] = [
  {
    id: 'cv_demo_1',
    title: '注意力和 RNN 的区别',
    model_id: null,
    created_at: '2026-10-19T20:11:00',
    updated_at: '2026-10-19T20:12:30',
    doc_ids: ['t_attention'],
    messages: [
      {
        id: 'm1', role: 'user', timestamp: '2026-10-19T20:11:02',
        content: '我读了你整理的这篇注意力笔记，能不能再讲讲它和 RNN 的根本差别？',
      },
      {
        id: 'm2', role: 'assistant', timestamp: '2026-10-19T20:12:30',
        doc_refs: [{ docId: 't_attention', title: 'Transformer 注意力机制到底在算什么' }],
        tool_calls: [{
          tool: 'search_docs',
          display: '检索知识库',
          summary: '命中《Transformer 注意力机制到底在算什么》',
        }],
        content: `根本差别是**信息传播的距离**，不是「谁更准」。

<!-- limo-block:v1 type="card-group" -->
### RNN：信息逐跳传递
每个时间步只能看到上一步的隐状态。第 100 个词想用第 1 个词的信息，得经过 99 次压缩 —— 每跳都会损失一点。

### 注意力：一步直达
任意两个位置之间是**直接连接**，路径长度恒为 1。这是它能建模长距离依赖的结构性原因。

### 代价
RNN 是 O(n) 计算、O(1) 内存；注意力是 O(n²) —— 长度翻倍，计算翻四倍。
<!-- /limo-block -->

所以「Transformer 更强」这个说法不准确：**在长序列上更强，在超长序列上更贵**。后来的线性注意力、状态空间模型都是在补这笔代价。`,
        follow_ups: [
          '那为什么说注意力可以并行训练？',
          '长上下文模型是怎么把 O(n²) 降下来的？',
          '位置编码为什么必须加？',
        ],
      },
    ],
  },
]

// ── 对话回答（按问题关键词伪分流，够展示形态即可） ──

function demoAnswer(question: string, docTitles: string[]): string {
  const q = question.toLowerCase()
  if (q.includes('注意力') || q.includes('attention') || q.includes('transformer')) {
    return `先说结论：注意力做的是一次**可学习的加权平均**，每个词按自己算出的权重，去别的词那里取信息。

<!-- limo-block:v1 type="card-group" -->
### 打分与归一
查询与每个键做点积得到「相关性」，再除以 $\\sqrt{d_k}$ 防止点积随维度膨胀，最后 softmax 成和为 1 的权重。

### 取值
用这组权重对值向量加权求和 —— 输出就是「按相关度混合后的信息」。
<!-- /limo-block -->

所谓「多头」，是同时学好几种这样的加权方式，再把结果拼起来。${docTitles.length ? `\n\n（回答参考了库内文档：《${docTitles[0]}》）` : ''}`
  }
  if (q.includes('文件夹') || q.includes('整理') || q.includes('分类')) {
    return `可以按三层来想：

<!-- limo-block:v1 type="how-to" -->
1. 先按**来源形态**分（视频 / 网页 / 论文）
2. 再按**主题**分（一个主题一个夹，允许嵌套）
3. 最后用**标签**做横向切面（跨文件夹的同一类东西）
<!-- /limo-block -->

文件夹是「放在哪」，标签是「属于哪类」—— 前者互斥，后者可以叠加。两边都想要的时候，先问自己：这个东西只会属于一个地方吗？`
  }
  if (q.includes('导出') || q.includes('pdf') || q.includes('word')) {
    return `支持四种格式，差别主要在**保真度**：

<!-- limo-block:v1 type="table" -->
| 格式 | 适合 | 注意 |
|---|---|---|
| Markdown | 二次编辑、进别的工具 | 图片可内嵌 |
| HTML | 分享、离线查看 | 完全自包含，断网可用 |
| Word | 交付、批注 | 公式保留 LaTeX 原文 |
| PDF | 打印、归档 | 与预览一致，自动分页 |
<!-- /limo-block -->

演示页里导出按钮是禁用的 —— 它需要一个真正的后端来渲染。`
  }
  return `（这是静态演示页）我这边没有模型可以真正回答，但可以看看界面能做什么：

<!-- limo-block:v1 type="info" -->
左侧是文档库，中间是预览区，右下角可以唤起 AI 对话。预览区顶部能切换「笔记 / 整理版原文 / 原始转录」。
<!-- /limo-block -->

试着问「注意力是怎么算的」「文件夹怎么组织」「能导出什么格式」，这几个有预置的示例回答。`
}

// ── 路由 ──

async function handleChatStream(question: string, docIds: string[]): Promise<Response> {
  const titles = docs.filter(d => docIds.includes(d.id)).map(d => d.title)
  const answer = demoAnswer(question, titles)
  const events: Record<string, unknown>[] = [
    { type: 'meta', conversation_id: 'cv_demo_1', context_window: 128000, budget: 89600 },
  ]
  if (titles.length > 0) {
    events.push(
      { type: 'agent_status', message: '正在检索知识库' },
      { type: 'tool_start', tool: 'search_docs', display: '检索知识库' },
      {
        type: 'tool_result',
        tool: 'search_docs',
        summary: `命中 ${titles.length} 篇：${titles.slice(0, 2).join('、')}`,
        refs: docs.filter(d => docIds.includes(d.id)).slice(0, 3)
          .map(d => ({ docId: d.id, title: d.title })),
      },
    )
  }
  // 按 12 字切块 —— 太细会让打字机看起来卡，太粗就看不出流式
  for (let i = 0; i < answer.length; i += 12) {
    events.push({ type: 'text', text: answer.slice(i, i + 12) })
  }
  events.push({ type: 'done', usage: { prompt_tokens: 1832, completion_tokens: 268 } })
  return sseText(events, 26)
}

function searchDocs(q: string): { id: string; title: string; snippet: string }[] {
  const kw = q.trim().toLowerCase()
  if (!kw) return []
  const hits: { id: string; title: string; snippet: string }[] = []
  for (const d of docs) {
    const raw = DEMO_RAW[d.id] || ''
    const text = `${d.title}\n${raw}`
    const idx = text.toLowerCase().indexOf(kw)
    if (idx < 0) continue
    const from = Math.max(0, idx - 40)
    const to = Math.min(text.length, idx + kw.length + 60)
    const before = text.slice(from, idx).replace(/\n/g, ' ')
    const hit = text.slice(idx, idx + kw.length)
    const after = text.slice(idx + kw.length, to).replace(/\n/g, ' ')
    hits.push({ id: d.id, title: d.title, snippet: `${from > 0 ? '…' : ''}${before}<mark>${hit}</mark>${after}${to < text.length ? '…' : ''}` })
  }
  return hits
}

async function route(method: string, path: string, url: URL, body: () => Promise<any>): Promise<Response> {
  const seg = path.split('/').filter(Boolean) // ['transcripts', 't_attention']

  // -- 健康检查 / 能力清单 --
  if (path === '/health') return json({ status: 'ok', version: (capabilitiesJson as any).version })
  if (path === '/capabilities') return json(capabilitiesJson)
  if (path === '/logs') return json({ logs: [] })
  if (path === '/config' && method === 'GET') return json({ target_language: 'zh', fetch: {}, agent_tools: {} })
  if (path === '/config') return readonly('保存设置')

  // -- 文档 --
  if (path === '/transcripts' && method === 'GET') {
    const q = url.searchParams.get('q')?.toLowerCase()
    const sourceType = url.searchParams.get('sourceType')
    const tag = url.searchParams.get('tag')
    const folderId = url.searchParams.get('folderId')
    let out = docs
    if (sourceType) out = out.filter(d => d.sourceType === sourceType)
    if (tag) out = out.filter(d => d.tags.includes(tag))
    if (folderId !== null && folderId !== undefined && folderId !== '') {
      // 含子树（与后端 include_descendants 语义一致：点一个夹 = 看它和它的后代）
      const scope = new Set([folderId])
      let grew = true
      while (grew) {
        grew = false
        for (const f of folders) {
          if (f.parentId && scope.has(f.parentId) && !scope.has(f.id)) { scope.add(f.id); grew = true }
        }
      }
      out = out.filter(d => scope.has(d.folderId || ''))
    }
    if (q) out = out.filter(d => d.title.toLowerCase().includes(q))
    return json(out)
  }

  if (path === '/transcripts/batch' && method === 'POST') {
    const payload = await body()
    const ids: string[] = payload?.ids || []
    const action: string = payload?.action || ''
    for (const id of ids) {
      const d = docs.find(x => x.id === id)
      if (!d) continue
      if (action === 'delete') {
        const i = docs.indexOf(d)
        if (i >= 0) docs.splice(i, 1)
      } else if (action === 'move') {
        d.folderId = payload.folderId || ''
      } else if (action === 'tag') {
        const add: string[] = payload.tags || []
        d.tags = Array.from(new Set([...d.tags, ...add]))
      }
    }
    return json({ affected: ids.length, failed: [] })
  }

  if (seg[0] === 'transcripts' && seg.length === 2) {
    const d = docs.find(x => x.id === seg[1])
    if (method === 'GET') {
      if (!d) return notFound('文档不存在（演示站只有预置的 10 篇）')
      return json({ ...d, rawText: DEMO_RAW[d.id] || '' })
    }
    if (method === 'PATCH') {
      if (!d) return notFound('文档不存在')
      const payload = await body()
      if (typeof payload?.title === 'string') d.title = payload.title
      if (Array.isArray(payload?.tags)) d.tags = payload.tags
      if (typeof payload?.folderId === 'string') d.folderId = payload.folderId
      return json({ status: 'ok' })
    }
    if (method === 'DELETE') {
      const i = docs.findIndex(x => x.id === seg[1])
      if (i >= 0) docs.splice(i, 1)
      return json({ status: 'ok' })
    }
  }

  if (seg[0] === 'transcripts' && seg[2] === 'retranscribe') return readonly('重新转录')

  // -- 产物（笔记 / 整理版原文）--
  if (seg[0] === 'markdown' && seg.length === 2) {
    const id = seg[1]
    const mode = (url.searchParams.get('mode') || 'notes') as 'notes' | 'polished'
    if (method === 'PUT') return readonly('保存编辑')
    const d = docs.find(x => x.id === id)
    const content = mode === 'polished' ? DEMO_POLISHED[id] : DEMO_NOTES[id]
    if (!d || !content) {
      return json({ code: 'MARKDOWN_NOT_FOUND', message: '这篇文档还没有这个产物', recoverable: false })
    }
    const createdAt = d.createdAt
    const chars = content.replace(/<!--[\s\S]*?-->/g, '').replace(/\s/g, '').length
    return json({
      id: `${id}_${mode}_v1`,
      transcriptId: id,
      content,
      style: 'plain',
      mode,
      version: 1,
      createdAt,
      chars,
      source: 'deepseek-v4',
      versions: demoVersions(id, mode, createdAt, chars),
    })
  }

  if (seg[0] === 'process-results' && seg.length === 2) {
    const id = seg[1]
    const results = id === 't_whiteboard'
      ? [{
        id: 'pr_whiteboard_extract',
        transcriptId: id,
        mode: 'extract',
        engine: 'qwen-vl-ocr',
        content: DEMO_RAW.t_whiteboard || '',
        createdAt: docs.find(d => d.id === id)?.createdAt || now(),
      }]
      : []
    return json({ results })
  }

  // -- 文件夹 --
  if (path === '/folders' && method === 'GET') return json(folders)
  if (path === '/folders' && method === 'POST') {
    const payload = await body()
    const name = String(payload?.name || '').trim()
    if (!name) return json({ code: 'FOLDER_EMPTY_NAME', message: '文件夹名不能为空', recoverable: true }, 400)
    const f: FolderEntry = {
      id: `fd_demo_${Date.now()}`, name, parentId: payload?.parentId || '',
      createdAt: now(), count: 0, totalCount: 0,
    }
    folders.push(f)
    return json(f)
  }
  if (path === '/folders/ensure-path' && method === 'POST') {
    const payload = await body()
    const raw = String(payload?.path || '').trim()
    if (!raw) return json({ code: 'FOLDER_EMPTY_PATH', message: '路径不能为空', recoverable: true }, 400)
    const segs = raw.split('/').map(s => s.trim()).filter(Boolean)
    let parentId = payload?.parentId || ''
    let createdIds: string[] = []
    for (const name of segs) {
      const hit = folders.find(f => f.name === name && (f.parentId || '') === parentId)
      if (hit) { parentId = hit.id; continue }
      const f: FolderEntry = {
        id: `fd_demo_${Date.now()}_${createdIds.length}`, name, parentId,
        createdAt: now(), count: 0, totalCount: 0,
      }
      folders.push(f)
      createdIds.push(f.id)
      parentId = f.id
    }
    return json({ id: parentId, segments: segs, created: createdIds })
  }
  if (seg[0] === 'folders' && seg.length === 2) {
    const id = seg[1]
    const f = folders.find(x => x.id === id)
    if (method === 'PATCH') {
      if (!f) return notFound('文件夹不存在')
      const payload = await body()
      if (typeof payload?.name === 'string' && payload.name.trim()) f.name = payload.name.trim()
      if (typeof payload?.parentId === 'string') f.parentId = payload.parentId
      return json(f)
    }
    if (method === 'DELETE') {
      if (!f) return notFound('文件夹不存在')
      const scope = new Set([id])
      let grew = true
      while (grew) {
        grew = false
        for (const x of folders) {
          if (x.parentId && scope.has(x.parentId) && !scope.has(x.id)) { scope.add(x.id); grew = true }
        }
      }
      let moved = 0
      for (const d of docs) if (d.folderId && scope.has(d.folderId)) { d.folderId = ''; moved++ }
      for (let i = folders.length - 1; i >= 0; i--) if (scope.has(folders[i].id)) folders.splice(i, 1)
      return json({ moved, deletedFolders: scope.size, deletedFolderIds: Array.from(scope) })
    }
  }

  // -- 全文搜索 --
  if (path === '/search') return json(searchDocs(url.searchParams.get('q') || '').slice(0, url.searchParams.get('limit') ? Number(url.searchParams.get('limit')) : 100))

  // -- 对话 --
  if (path === '/chat/stream') {
    const question = url.searchParams.get('question') || ''
    const docIds = (url.searchParams.get('doc_ids') || '').split(',').filter(Boolean)
    return handleChatStream(question, docIds)
  }
  if (path === '/conversations' && method === 'GET') {
    return json(conversations.map(c => ({
      id: c.id, title: c.title, model_id: c.model_id,
      created_at: c.created_at, updated_at: c.updated_at,
    })))
  }
  if (path === '/conversations' && method === 'POST') {
    const payload = await body()
    const c: DemoConversation = {
      id: `cv_demo_${Date.now()}`, title: payload?.title || '新对话', model_id: payload?.model_id ?? null,
      created_at: now(), updated_at: now(), messages: [], doc_ids: payload?.doc_ids || [],
    }
    conversations.unshift(c)
    return json(c)
  }
  if (seg[0] === 'conversations' && seg.length === 2) {
    const c = conversations.find(x => x.id === seg[1])
    if (method === 'GET') return c ? json(c) : notFound('会话不存在')
    if (method === 'DELETE') {
      const i = conversations.findIndex(x => x.id === seg[1])
      if (i >= 0) conversations.splice(i, 1)
      return json({ status: 'ok' })
    }
    if (method === 'PATCH') {
      if (!c) return notFound('会话不存在')
      const payload = await body()
      if (typeof payload?.title === 'string') c.title = payload.title
      if (Array.isArray(payload?.doc_ids)) c.doc_ids = payload.doc_ids
      return json(c)
    }
  }
  if (seg[0] === 'conversations' && seg[2] === 'messages' && method === 'PUT') {
    // 消息 rebuild（编辑 / 删除 / 分支截断）—— 内存里生效
    const c = conversations.find(x => x.id === seg[1])
    const payload = await body()
    if (c && Array.isArray(payload?.messages)) c.messages = payload.messages
    return json({ status: 'ok' })
  }
  if (seg[0] === 'conversations' && seg[2] === 'rename' && method === 'POST') {
    const c = conversations.find(x => x.id === seg[1])
    const payload = await body()
    if (c && typeof payload?.title === 'string') c.title = payload.title
    return json({ status: 'ok' })
  }
  if (seg[0] === 'conversations' && seg[2] === 'clear-context') {
    return json({ status: 'ok' })
  }

  // -- 明确不支持的写路径（会花钱 / 需要真实后端）--
  const readonlyLabels: [RegExp, string][] = [
    [/^\/ingest/, '导入内容'],
    [/^\/generate\//, '生成笔记'],
    [/^\/process\//, '图片处理'],
    [/^\/classify\//, '智能分类'],
    [/^\/synthesis\//, '多篇合成'],
    [/^\/epub\//, 'EPUB 解读'],
    [/^\/references/, '暂存参考文件'],
    [/^\/export/, '导出'],
    [/^\/plugins/, '插件管理'],
    [/^\/(providers|models|scene-models|engine-|llm\/)/, '修改模型配置'],
  ]
  for (const [re, label] of readonlyLabels) {
    if (re.test(path)) return readonly(label)
  }

  // 兜底：宁可显式 404 让开发者看见漏了哪个端点，也不要假装成功
  return notFound(`演示站未实现这个接口：${method} ${path}`)
}

/** 安装 mock —— 幂等，重复调用无副作用（React StrictMode 会重跑副作用） */
export function installDemoFetch(): void {
  const w = window as unknown as Record<string, unknown>
  if (w[DEMO_FLAG]) return
  w[DEMO_FLAG] = true

  // 首屏直接落到一篇内容最完整的笔记上（演示站独立域，localStorage 与正式版不冲突）。
  // 顺手跳过首次启动向导 —— 那是「配置你自己的模型」流程，演示站没有后端可配，
  // 停在那一屏会让访客以为站点坏了。想看向导可清空本站 localStorage 再刷新。
  try {
    if (!localStorage.getItem('mbw_lastDocId')) localStorage.setItem('mbw_lastDocId', 't_attention')
    if (!localStorage.getItem('limo_setup_done')) localStorage.setItem('limo_setup_done', '1')
  } catch { /* 隐私模式下 localStorage 可能不可写 */ }

  const realFetch = window.fetch.bind(window)
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    let url: URL
    try {
      const raw = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      url = new URL(raw, window.location.href)
    } catch {
      return realFetch(input as RequestInfo, init)
    }

    // BASE 是绝对路径 '/api'，但部署在子路径（GitHub Pages）时也可能带前缀
    const idx = url.pathname.indexOf('/api/')
    if (idx < 0) return realFetch(input as RequestInfo, init)
    const path = url.pathname.slice(idx + 4) // '/api/health' → '/health'
    const method = (init?.method || (typeof input !== 'string' && !(input instanceof URL) ? input.method : 'GET')).toUpperCase()

    const body = async () => {
      try {
        const raw = init?.body
        if (typeof raw === 'string') return JSON.parse(raw)
        return {}
      } catch { return {} }
    }

    return route(method, path, url, body)
  }
}
