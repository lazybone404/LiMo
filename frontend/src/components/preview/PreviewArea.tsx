import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useStore } from '../../store'
import { getTranscript, getMarkdown, saveMarkdownContent, fetchProcessResults, apiUrl, getErrorMessage, ApiErrorException } from '../../api/client'
import ReactMarkdown from 'react-markdown'
import { REMARK_PLUGINS, REHYPE_PLUGINS } from '../../lib/markdown-plugins'
import { createMarkdownComponents } from '../../lib/markdown-components'
import { getStreamBuffer } from '../../store/streamBuffers'
import type { MarkdownMeta, MarkdownVersion, TaskInfo } from '../../store/types'
import { TabRegistry, type TabDef } from '../../lib/tabRegistry'
import { getContentForView } from '../../lib/contentSources'
import { ScanText, Table, ScanLine, Eye, ChevronRight, Terminal, Image, Package, AlertTriangle, FileText, Columns, AlignCenter, Maximize2, Minimize2, Expand, Pencil, Highlighter, LayoutGrid } from 'lucide-react'
import Zoom from 'react-medium-image-zoom'
import 'react-medium-image-zoom/dist/styles.css'
import { getTypeMeta } from '../gen-panel/constants'
import { CONTENT_WIDTH_RATIO, computeContentMaxW, measurePxOf } from '../../lib/reading-prefs'
import { planArtifactRequests } from '../../lib/artifactModes'
import { VideoBanner } from './VideoBanner'
import { DocTabBar } from './DocTabBar'
import { LogPanel } from '../ui/LogPanel'
import { ExportMenu } from '../ui/ExportMenu'
import { ResizeHandle } from '../ui/ResizeHandle'
import { TOC } from './TOC'
import MindmapView from './MindmapView'
import { TranscriptView } from './TranscriptView'
import { MarkdownPreview } from './MarkdownPreview'
import { CardSections } from './CardSections'
import { MarkerContent } from './MarkerContent'
import { TextareaMarkdownEditor } from './TextareaMarkdownEditor'
import { PartialBanner } from './PartialBanner'
import { UnsavedChangesDialog } from './UnsavedChangesDialog'
import { EmptyState } from './EmptyState'
import { ProcessingPlaceholder } from './ProcessingPlaceholder'
import { VersionSwitcher } from './VersionSwitcher'
import { ArtifactMetaBar } from './ArtifactMetaBar'
import { TagSuggestions } from './TagSuggestions'
import { TranscriptQualityNote } from './TranscriptQualityNote'
import { ImageResultView } from './ImageResultView'
import { CopyContentBtn } from './CopyContentBtn'
import 'katex/dist/katex.min.css'
import { IMAGE_MODES, IMAGE_MODE_LABELS, type ImageMode } from '../../lib/imageModes'
import { toMarkdownMeta } from '../../lib/markdownMeta'
import { ChatFollowUpList } from '../ai-chat/ChatMarkerContent'
import { useChatStore } from '../../store/chatStore'

/**
 * OCR 空态四项提示的图标 —— 按 mode **键**组织，不用数组下标。
 * ⚠️ 下标写法（`[ScanText, Table, ...][i]`）把「哪个图标配哪个模式」变成**位置记忆**：
 * 谁调整了 `IMAGE_MODES` 的顺序，图标就整体错配一格，**零报错**（模式 #54 的形态）。
 */
const IMAGE_MODE_ICONS: Record<ImageMode, typeof ScanText> = {
  extract: ScanText, table: Table, digitize: ScanLine, understand: Eye,
}

// ── 欢迎文档（空态示例文档）内容 ──
// 对齐 LiMo 双产物 + 转录结构：笔记（欢迎/简介）· 整理版原文（开发者全面功能）· 转录（更新记录）。
// 新增/移除功能时同步更新（模式 34：欢迎文档面向新用户展示当前能力）。
// ⚠️ 三份内容都是**模板字符串**：正文里不能出现反引号——它会把字符串提前终止，
//    而报错发生在解析阶段（整个模块加载失败），症状与「我改的那句话」毫无关系。
//    要写快捷键就写 Ctrl+K，不要写成带反引号的代码样式。同理别写 ${。
const WELCOME_TABS: { key: 'notes' | 'polished' | 'transcript'; label: string }[] = [
  { key: 'notes', label: '笔记' },
  { key: 'polished', label: '整理版原文' },
  { key: 'transcript', label: '转录' },
]

const WELCOME_NOTES = `## 欢迎使用 LiMo

> 知识管理系统：把任何来源（网页、视频、PDF、Word、图片…）变成**可读、可搜索、可复用的结构化知识**。

### 快速开始

1. **导入内容** — 粘贴网页/视频链接，或拖入 PDF、Word、PPT、Excel、EPUB、图片、视频
2. **自动转录** — 自动提取正文、语音转文字、OCR 识别；视频可开启「画面理解」
3. **生成笔记** — 一键生成「笔记」+「整理版原文」两份 Markdown，可附参考资料；勾选「图表」让 AI 输出流程图/架构图，本地渲染 PNG 嵌入
4. **编辑产物** — 笔记/整理版可手动编辑（源码 + 分屏预览），保存覆盖并保留上一版
5. **整理归档** — 左侧文档库：**排列方式**（平铺 / 按文件夹 / 按类型 / 按时间）与**排序**（时间 / 标题 / 类型 / 相关度）是两个独立旋钮；筛选区可折叠（文件夹范围 / 文件类型 / 标签 / 标题关键词 / 日期范围）；全文搜索搜正文而不只是标题，按 Ctrl+K 可从任意位置直达搜索框
6. **更多能力** — 对话 AI 查资料、导出 HTML/MD/Word/PDF/ZIP/思维导图

> 切换到「整理版原文」查看面向开发者的完整功能；切到「转录」查看最近更新记录。`

const WELCOME_POLISHED = `## LiMo 功能总览（开发者向）

### 内容导入
- **多源输入**：网页 / 视频 / 纯文本 / Markdown / 本地文件（PDF、Word、PPT、Excel、EPUB、图片、视频，≤2GB）
- **URL 导入**：粘贴链接自动提取正文；B站/YouTube Cookie 注入支持平台认证
- **双层去重**：URL 级拦截 + 内容 SHA256 校验，重复导入自动选中已有文档

### 文档库
- **排列 ⊥ 排序**：排列（平铺 / 按文件夹 / 按类型 / 按时间，IDE 式一体化树）与排序（时间 / 标题 / 类型 / 相关度）互不干扰——筛选不会让分组消失，而是「按文件夹分组、每组只显符合条件的」
- **可折叠筛选区**：文件夹范围 / 文件类型 / 标签（全量，无上限）/ 标题关键词（纯内存，不走全文检索）/ 日期范围；两级折叠、收起时仍显示激活条件摘要；筛选区与列表区之间的分隔条可拖拽调比例
- **文件夹分类**：单层逻辑分类（不映射磁盘目录，不破坏既有文档目录结构），拖拽移动 + 右键菜单；删除文件夹只把文档移回未分类
- **全文搜索**：标题/类型即时过滤 + 正文 FTS5 检索（消抖合并），命中并入主列表并标注「原文」角标 + 正文摘要片段；相关性序保留为「相关度」排序键；Ctrl+K 直达搜索框
- **多选与批量**：复选框常驻，单击是打开文档（不动选择集），Ctrl/Shift 多选、Ctrl+A 全选；批量删除/移动/打标签；大列表分批渲染（滚动自动加载）
- **密度**：**详细**（卡片，看封面与元数据）/ **简略**（26px 单行，**按文件夹呈现为 IDE 文件树**：文件夹可折叠、文档缩进一层，一屏看更多）

### 转录与引擎
- **ASR**：bcut（B站必剪免费云转写）+ DashScope Paraformer（云端）；本地 whisper 可经插件接入
- **OCR**：DashScope 云端（图片转文字、扫描件提取）；Tesseract 本地 OCR 经插件模板扩展
- **文档引擎**：pypdfium2（PDF 毫秒级）+ pdf-image（扫描件整页转图）+ MarkItDown / python-docx / python-pptx / openpyxl / epub-container（自研 EPUB）；AGPL 引擎（如 pymupdf4llm）以插件进程外接入
- **视频处理**：ffmpeg 抽帧 + 网格拼接 + 模糊帧过滤（Laplacian 方差 + 熵 + 边缘密度）

### LLM 生成
- **双产物并行**：notes（结构化笔记）+ polished（整理版原文），asyncio 并发 + SSE 逐 token 流式
- **按类型派发**：视频/网页/文档/Excel 走不同 prompt 模板；9+8 种笔记风格可选
- **思考链**：deepseek-v4 reasoning_content 逐段展示「模型思考中」；思考强度可选快速/标准/最强（**整理版固定快速**，选「最强」才一起深度思考）
- **PDF 直传**：把 PDF 原件直接发给模型阅读（本地抽取的文本不进提示词），与文本模式二选一；扫描件/图片型 PDF 效果更好
- **自动续写**：length 截断自动续写 3 轮，temperature 递减
- **参考文件**：注入参考资料（如评论区观点）提升生成质量；超出模型上下文时自动先压缩
- **图表**：勾选后 AI 只输出语义 JSON（9 种结构：流程/层级/时间线/依赖/分类树/判定流/状态机/时序/四象限）→ 本地算坐标绘制 PNG 嵌入笔记

### 图片处理
- 4 种模式：文字提取 / 表格识别 / 文档数字化 / 内容理解，LLM 优先 → 引擎回退

### 对话 AI
- 5 个工具：文档检索 / 文档查询 / 文档列表 / 网页搜索（Tavily → SearXNG → DDG 三级降级）/ 网页抓取
- 上下文感知预算 + 长对话 LLM 压缩 + 消息级文档引用（@ 选文档）

### 插件系统
- EnginePlugin v1（默认进程外 + trusted 例外），官方零维护，一键安装依赖

### 导出
- MD / HTML（KaTeX 预渲染、零 CDN、自包含）/ **Word（.docx，标题大纲+表格+图片内嵌）** / **PDF（桌面版直接落文件，免打印对话框）** / ZIP 批量 / 思维导图（SVG + 交互式 HTML）/ 对话导出

### 体验与安全
- 六轴主题系统（明暗/配色/基调/纹理/字体）+ 一键主题预设
- 多语言输出、SSRF/路径遍历/XSS 防护、并发信号量控制、全链路请求日志追踪

> 完整架构与设计决策见项目 docs/ 目录（architecture.md、AGENT-ONBOARDING.md 等）。`

const WELCOME_TRANSCRIPT = `## 更新记录

> 按时间倒序，最近更新在最前。完整记录见项目 docs/CHANGELOG.md。

- **2026-09-25** — 文档库交互重设计：排列与排序拆成两个独立旋钮 + 可折叠筛选区 + 常态多选（单击仍是打开文档）+ 相关度排序键 + Ctrl+K
- **2026-09-23** — 文档库组织能力（文件夹/全文搜索/排序筛选/批量操作）+ DeepSeek 思考档位 + PDF 直传
- **2026-09-23** — 参考文件：注入文案重写、上限放宽至 1MB、超窗自动压缩
- **2026-09-21** — PDF 导出（WebView2 PrintToPdf / 打印对话框）+ Word（.docx）导出
- **2026-09-19** — 图表升级：语义化布局（LLM 只写 JSON）+ 多图 + 类型扩到 9 种
- **2026-08-26** — EPUB 一键解读（自研解析器替代 ebooklib）+ 依赖合规清理
- **2026-08-25** — 视频直传（DashScope 原生通道，服务端抽帧）
- **2026-08-16** — 图表作为笔记可选组件 + AI 审核（fact_check）移除转历史
- **2026-08-15** — 产物编辑能力：笔记/整理版手动编辑（源码 + 分屏预览 + 覆盖保护）
- **2026-08-14** — 插件 venv 检查路径遍历异常修复
- **2026-08-14** — 打包体积优化：docling 清理 + ffmpeg 精简（安装包 -44%）
- **2026-08-14** — 文档全面更新 + AGENT-ONBOARDING 上手文档
- **2026-08-13** — 首次体验与健壮性：持续探活 + 请求日志 + 配置向导 + 缺 key 提示
- **2026-08-06** — PDF 笔记图片显示修复 + 插件 venv 健壮性修复
- **2026-08-04** — 对话上下文管理（C1-C5）+ 对话模块全量重写（P1-P6）
- **2026-08-03** — 视频笔记 prompt 过滤规则补充`

/** PreviewArea 主组件 — 内容预览 + Tab 切换 + 工具栏 */
export function PreviewArea() {
  const activeView = useStore(s => s.activeView)
  const setActiveView = useStore(s => s.setActiveView)
  const currentDocId = useStore(s => s.currentDocId)
  const currentContent = useStore(s => s.currentContent)
  const currentPolishedContent = useStore(s => s.currentPolishedContent)
  const currentRawText = useStore(s => s.currentRawText)
  const markdownMeta = useStore(s => s.markdownMeta)
  const markdownVersions = useStore(s => s.markdownVersions)
  // 订阅 streamTick 以在 flush 时重读 getStreamBuffer（Module Map 非响应式）
  useStore(s => s.streamTick)
  const isStreaming = useStore(s => {
    if (!s.currentDocId) return false
    return !!s.processingTasks[s.currentDocId + '__generate']
  })
  const contentWidth = useStore(s => s.contentWidth)
  const cycleContentWidth = useStore(s => s.cycleContentWidth)
  const immersive = useStore(s => s.immersive)
  const toggleImmersive = useStore(s => s.toggleImmersive)
  const reading = useStore(s => s.reading)
  // 产物骨架轴（`paper` / `card`）—— **只在 notes/polished 的只读态消费**（唯一接入点）。
  // ⚠️ 刻意不在 MarkdownPreview 内部按骨架分派：那会静默扩散到 Excel 数据摘要与
  //    原始转录两处调用点，而它们都不是 LLM 产物。
  const skeleton = useStore(s => s.skeleton)
  const setCard = useStore(s => s.setCard)
  // 语义标记的装饰开关（`limo-block`）。⚠️ 与 skeleton 不同，它**只影响装饰**：
  // 关掉后块内文字照常显示，只是没有左边框/淡底（见 lib/marker-prefs.ts 文件头）。
  const markerDecor = useStore(s => s.markerDecor)
  const setMarker = useStore(s => s.setMarker)

  // ratio 取自 lib/reading-prefs 的 CONTENT_WIDTH_RATIO（单源）——本表只补 label/icon
  const WIDTH_RATIO: Record<string, { label: string; icon: typeof Columns; ratio: number }> = {
    narrow: { label: '紧凑', icon: AlignCenter, ratio: CONTENT_WIDTH_RATIO.narrow },
    normal: { label: '标准', icon: Columns, ratio: CONTENT_WIDTH_RATIO.normal },
    wide:   { label: '宽屏', icon: Maximize2, ratio: CONTENT_WIDTH_RATIO.wide },
  }

  // 每行字数 → 版宽上限（px）。null = 不设上限（沿用比例制，零回归）。
  // 换算收口在 `measurePxOf`（纯函数、可单测）：它乘的是 `fontScale`（正文的**实际**字号），
  // **不是** `fontSizeScale[contentWidth]`（那是宽度档的档感，与字号无关）。漏乘 `fontScale`
  // 的方向是反的：字号调到 160% 后仍按 16px 算成 640px，而实际只放得下 25 字 —— 越放大越挤。
  const measurePx = measurePxOf(reading.measure, reading.fontScale)

  // ResizeObserver 追踪预览面板实际宽度
  const containerRef = useRef<HTMLDivElement>(null)
  const [containerWidth, setContainerWidth] = useState(800)
  useEffect(() => {
    const el = containerRef.current?.parentElement
    if (!el) return
    const ro = new ResizeObserver(([entry]) => {
      setContainerWidth(entry.contentRect.width)
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // ── 正文字号缩放：Ctrl + 滚轮 ──
  // ⚠️ **必须用原生 `addEventListener(..., { passive: false })`，不能用 React 的 onWheel**：
  // React 把 `wheel` / `touchstart` / `touchmove` 按 **passive** 注册在 root 上，在处理器里调
  // `e.preventDefault()` 只会得到一条控制台警告，**拦不住** WebView2 把整个页面（连文档库、
  // 工具栏一起）缩放 —— 那正是要阻止的行为。
  // ⚠️ 挂在**预览区容器**（而非 window）：字面落实「只对预览栏生效」，也避免鼠标停在
  // 文档库列表上按到 Ctrl+滚轮时误触。
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey) return
      e.preventDefault()
      // 复用 store 的跨 slice 动作（与键盘路径同一个函数、同一份档位换算与 toast）
      useStore.getState().stepFontScale(e.deltaY < 0 ? 1 : -1)
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [])

  // 内容上限动态跟随面板宽度：小窗口保底 1100px；折叠 GenPanel/DocLibrary 后面板变宽 →
  // 内容同步放宽（两侧各留 ~80px 边距）利用空白，而不是被固定 1100 钳制。
  // 阅读版式另加一条**度量上限**（每行字数）：设置页调过时与比例值取小；没调过（null）
  // 时逐值复现改造前的行为。数学见 lib/reading-prefs.ts 的 computeContentMaxW（纯函数、可单测）。
  const contentMaxW = `${computeContentMaxW(containerWidth, WIDTH_RATIO[contentWidth].ratio, measurePx)}px`
  const fontSizeScale: Record<string, number> = { narrow: 0.92, normal: 1.0, wide: 1.06 }
  const setCurrentContent = useStore(s => s.setCurrentContent)
  const setCurrentPolishedContent = useStore(s => s.setCurrentPolishedContent)

  const setCurrentRawText = useStore(s => s.setCurrentRawText)
  const setMarkdownMeta = useStore(s => s.setMarkdownMeta)
  const setMarkdownVersions = useStore(s => s.setMarkdownVersions)
  const docs = useStore(s => s.docs)
  const imageResults = useStore(s => s.imageResults)
  const loadImageResults = useStore(s => s.loadImageResults)
  const tocWidth = useStore(s => s.slots.toc.width)
  const tocCollapsed = useStore(s => s.slots.toc.collapsed)
  const setSlot = useStore(s => s.setSlot)

  const currentDoc = docs.find(d => d.id === currentDocId)
  // ⚠️ **必须是 `s.streaming`**（对话正在跑），**不是** `s.generating`（那位的语义是
  // 「本轮要生成」而非「正在流式」）。两者在同一个 `set` 里、词形相近，极易取错一个 ——
  // 取错的表现是 chip 的禁用态与 `dispatchSend` 首行 `if (s.streaming) return` **不同源**：
  // 界面看起来能点，点了却什么都不发生。`artifactFollowUp.test.tsx` 有对照实验钉住。
  const chatStreaming = useChatStore(s => s.streaming)

  // 追问 chip 点击：**打开 AI 浮窗 + 带着这篇文档直接发问**。
  // ⚠️ **必须显式传 `docRefs`**：对话气泡里那个 chip 刻意不传（它是当前会话的延续，
  // 「当前选中文档」由 `dispatchSend` 自动带上），语义正确；而这里是从预览区发起，
  // 「就这篇文档发问」才是用户的真实意图 —— 不传会退化成「向 AI 提一个孤零零的问题」。
  const handleFollowUpSelect = useCallback((q: string) => {
    // ⚠️ 守卫不是防御性习惯：`currentDocId` 的类型是 `string | null`，而没有当前文档时
    // 追问卡压根不渲染 —— 早退把「类型上可能为空」与「实际不会为空」两件事对齐，
    // 而不是用 `currentDocId || ''` 去伪造一个不存在的 docId。
    if (!currentDocId) return
    useStore.getState().setAiVisible(true)
    useChatStore.getState().sendMessage(q, [{ docId: currentDocId, title: currentDoc?.title || '' }])
  }, [currentDocId, currentDoc?.title])
  const sourceUrl = currentDoc?.sourceUrl
  const isVideo = currentDoc?.sourceType === 'video'
  const imageMarkdownComponents = useMemo(() => createMarkdownComponents(sourceUrl, isVideo), [sourceUrl, isVideo])

  // TOC 拖拽回调
  const resizeToc = useCallback((dx: number) => {
    const w = useStore.getState().slots.toc.width
    setSlot('toc', { width: Math.max(60, w + dx) })
  }, [setSlot])

  // 加载指定版本的 markdown
  const [versionLoading, setVersionLoading] = useState(false)
  const [showLogs, setShowLogs] = useState(false)
  const [contentLoadError, setContentLoadError] = useState(false)
  // 欢迎文档（空态示例文档）内部 tab：笔记 / 整理版原文 / 转录 —— 对齐真实文档的双产物 + 转录结构
  const [welcomeTab, setWelcomeTab] = useState<'notes' | 'polished' | 'transcript'>('notes')

  // ── 产物编辑状态（notes/polished 手动编辑，见 pi-docs/产物编辑方案.md）──
  // 编辑态绑定来源文档 id：所有无参保存路径（Ctrl+S / 保存按钮）必须以编辑内容的
  // 真实归属文档为目标，而非 currentDocId——弹窗打开时 currentDocId 已被 selectDoc 切成
  // 新文档，用 currentDocId 会把 A 的编辑内容错存进 B（评审发现的数据污染根因）。
  // `version` 是**开始编辑时用户正在看的那一版**——编辑就地覆盖它（不产生新版本）。
  const [editing, setEditing] = useState<{ mode: 'notes' | 'polished'; docId: string; version: number } | null>(null)
  const [editValue, setEditValue] = useState('')
  const [editDirty, setEditDirty] = useState(false)
  const [savingEdit, setSavingEdit] = useState(false)
  // 未保存离开三态弹窗：type='tab' → target=目标 tab；type='doc' → target=目标文档 id，prevDocId=原文档 id
  const [unsavedDialog, setUnsavedDialog] = useState<{ type: 'tab' | 'doc'; target?: string; prevDocId?: string } | null>(null)
  const [dialogSaving, setDialogSaving] = useState(false)
  const setMarkdownEdited = useStore(s => s.setMarkdownEdited)
  const markdownEdited = useStore(s => s.markdownEdited)
  // 编辑中未保存 → 暴露到 store（GenPanel 重新生成入口拦截用）。只暴露归属文档 id，
  // 不提升编辑态本身。外部（重新生成放弃编辑）清空标记时由下方 effect 自关闭编辑态。
  const dirtyEditingDocId = useStore(s => s.dirtyEditingDocId)
  const setDirtyEditingDocId = useStore(s => s.setDirtyEditingDocId)
  // 切文档防丢：记录上一次文档 id（取消切换时回退）
  const prevDocIdRef = useRef<string | null>(null)
  /**
   * 每个 mode **各自**最近一次从后端拿到的产物元数据与版本列表。
   *
   * ⚠️ 为什么必须按 mode 分桶：`markdownMeta` / `markdownVersions` 是**单值**的，
   * 而 notes 与 polished 是两个独立产物位、版本号各自从 v1 起。它们在两个 tab 之间
   * 切换时若不同步，会同时坏掉两件事：
   *   - 版本下拉显示**另一个 mode** 的版本列表（切到整理版看到笔记的版本）；
   *   - 编辑「就地覆盖」时把另一个 mode 的版号用到这一版上 —— 2026-10-10 引入就地
   *     覆盖语义后，这条从「多留一版」升级成**覆盖掉用户没在看的版本、原文不可恢复**
   *     （P1，本轮修复；`docs/code-audit-3.md`）。
   * 本 ref 是「每个 mode 的真相」，store 里的那两个值是「当前 tab 的视图」。
   */
  const perModeMetaRef = useRef<Partial<Record<'notes' | 'polished',
    { meta: MarkdownMeta; versions: MarkdownVersion[] }>>>({})
  /** 最近一次版本切换请求的归属（文档 + mode + 版本号）—— 见 `loadVersion` 的竞态说明。 */
  const versionReqRef = useRef<{ docId: string; mode: 'notes' | 'polished'; version: number } | null>(null)
  // 弹窗「取消」回退后，下一次 effect 需跳过清理+加载（保留原文档编辑态与未保存内容）
  const skipDocLoadRef = useRef(false)

  /**
   * 切 tab 时把 `markdownMeta` / `markdownVersions` 同步成**那个 mode 自己的**
   * （理由见 `perModeMetaRef`）。只对 notes / polished 两个产物 tab 生效 ——
   * 转录/图片/思维导图不持有产物版本概念，不该被改写成某一个 mode 的。
   *
   * ⚠️ 覆盖「ref 已在、但 effect 不重跑」的缺口：产物是**异步**加载后才写进 ref 的，
   * 那一次由加载分支自己负责同步（见 `loadDocContent`）；本 effect 管的是**之后**的
   * 用户切换。
   */
  useEffect(() => {
    // ⚠️ 判据走 `TabDef.htmlMode`，**不是** activeView 字面量：笔记 tab 的 key 是
    //    `'summary'` 而不是 `'notes'`，写成 `activeView !== 'notes'` 编译期就会红
    //    （tsc 抓到了），而写成别的差不多的字面量则**不报错、只是这个 effect 永不生效**
    //    ——那就是「修了但没修上」。`htmlMode` 是 tabRegistry 里「此 tab 对应哪个产物
    //    mode」的唯一定义，用它顺带自动排除转录/思维导图/图片等无产物版本的 tab。
    const mode = TabRegistry.get(activeView)?.htmlMode
    if (mode !== 'notes' && mode !== 'polished') return
    const cached = perModeMetaRef.current[mode]
    if (cached) {
      setMarkdownMeta(cached.meta)
      setMarkdownVersions(cached.versions)
    } else if (markdownMeta && markdownMeta.mode === mode) {
      // 自举：视图值已属于这个 mode（生成完成时 GenPanel / 恢复态 App.tsx 直接写进
      // store，**没经过本组件**；F4 早退路径同理），但 ref 里没有记录。认领它 ——
      // 否则下次从另一个 tab 切回来，会读到更早的陈旧值。
      perModeMetaRef.current[mode] = { meta: markdownMeta, versions: markdownVersions }
    }
  }, [activeView, markdownMeta, markdownVersions, setMarkdownMeta, setMarkdownVersions])

  /** 进入编辑模式（summary tab → notes；polished tab → polished） */
  const startEditing = () => {
    if (!currentDocId || !displayContent) return
    const mode: 'notes' | 'polished' = activeView === 'polished' ? 'polished' : 'notes'
    // ⚠️ 必须记下**用户此刻正在看的那一版**：编辑是就地覆盖（不产生新版本），保存时
    // 靠它定位目标。切 tab 时 `markdownMeta` 已由 `perModeMetaRef` 的同步 effect 换成
    // **这个 mode 自己的**元数据（含用户在该 tab 切过的版本），故下面的判据恒成立。
    // ⚠️ 兜底 `0`（= 最新版）**不是无害的默认值**：就地覆盖语义下它会把用户没在看的那
    // 一版改掉、原文不可恢复（audit-3 P1 正是这条路径）。它是最后一道防线，不是正常路径。
    const version = markdownMeta && markdownMeta.mode === mode ? markdownMeta.version : 0
    setEditValue(displayContent)
    setEditDirty(false)
    setEditing({ mode, docId: currentDocId, version })
  }

  const cancelEditing = () => {
    setEditing(null)
    setEditDirty(false)
  }

  /**
   * 保存编辑：PUT /api/markdown → 更新内容/meta/versions + 置 edited 标记。
   * **就地覆盖 `editing.version` 那一版**（版本数不变、版本号不变）——编辑不产生版本。
   * 返回 boolean（是否保存成功）——「保存并离开」需据此决定是否放行离开。
   * saveDocId：覆盖保存目标文档。切文档「保存并离开」时 currentDocId 已变为新文档，
   *   必须传入原文档 id（prevDocId）否则内容会存到错误文档。注意不要求 editDirty——
   *   弹窗「保存并离开」即使内容与原文一致也应保存。
   * 目标文档优先级：显式 saveDocId > 编辑态来源 editing.docId > currentDocId——
   *   编辑态来源保证 Ctrl+S/保存按钮永远写回编辑内容的真正归属文档。
   */
  const handleSaveEdit = async (saveDocId?: string): Promise<boolean> => {
    // 防御：onClick 等 React 事件处理器可能把 MouseEvent 当首个参数传入（TS 无参函数
    // 赋给 onClick 逆变兼容不报错）——saveDocId 必须为 string 才视为显式目标，否则回退编辑态归属文档。
    const docId = typeof saveDocId === 'string' ? saveDocId : (editing?.docId || currentDocId)
    if (!editing || !docId || savingEdit) return false
    // 弹窗打开时拒绝无参保存：弹窗遮罩覆盖编辑器，Ctrl+S 属编辑器快捷键不应生效；
    // 「保存并离开」走 handleDialogSave 显式传 saveDocId，不受此拦截。
    if (typeof saveDocId !== 'string' && unsavedDialog) return false
    setSavingEdit(true)
    try {
      const md = await saveMarkdownContent(docId, editing.mode, editValue, editing.version)
      if (editing.mode === 'polished') {
        setCurrentPolishedContent(md.content)
      } else {
        setCurrentContent(md.content)
      }
      // `edited` 不再写死 true：它由后端从文件头派生后带回（`md.edited`）。
      // 写死就是在响应里放第二份判据（模式 #54）；映射收口在 `toMarkdownMeta`。
      setMarkdownMeta(toMarkdownMeta(md))
      setMarkdownVersions(md.versions || [])
      setMarkdownEdited(editing.mode, true)
      setEditing(null)
      setEditDirty(false)
      useStore.getState().addToast('已保存', 'success')
      return true
    } catch (err) {
      useStore.getState().addToast(getErrorMessage(err), 'error')
      return false
    } finally {
      setSavingEdit(false)
    }
  }

  /** 切换 tab：编辑中且有未保存修改 → 三态弹窗（保存并离开 / 不保存 / 取消） */
  const handleTabSwitch = (key: string) => {
    if (key === activeView) return
    if (editing && editDirty) {
      setUnsavedDialog({ type: 'tab', target: key })
      return
    }
    if (editing) { setEditing(null); setEditDirty(false) }
    setActiveView(key)
  }

  // ── 未保存离开三态弹窗回调 ──
  // 保存并离开：保存成功才放行（失败留在当前，弹窗保持可重试）
  const handleDialogSave = async () => {
    const dlg = unsavedDialog
    if (!dlg) return
    setDialogSaving(true)
    // 文档切换时 currentDocId 已是新文档——保存必须针对编辑内容真正归属的文档。
    // 优先 editing.docId（编辑态来源，弹窗可能被二次切文档覆盖成中间文档）；
    // 兜底弹窗记录的 prevDocId。
    const saveDocId = dlg.type === 'doc' ? (editing?.docId || dlg.prevDocId) : currentDocId
    const ok = await handleSaveEdit(saveDocId ?? undefined)
    if (!ok) { setDialogSaving(false); return }
    const { type, target } = dlg
    setUnsavedDialog(null)
    setDialogSaving(false)
    if (type === 'tab') setActiveView(target!)
    else loadDocContent(target!)   // 文档切换：拦截时 currentDocId 已变为新 id，直接加载
  }
  // 不保存：丢弃未保存修改，直接离开
  const handleDialogDiscard = () => {
    const dlg = unsavedDialog
    if (!dlg) return
    setEditing(null)
    setEditDirty(false)
    const { type, target } = dlg
    setUnsavedDialog(null)
    if (type === 'tab') setActiveView(target!)
    else loadDocContent(target!)
  }
  // 取消：留在当前。文档切换场景回退到编辑内容的归属文档（编辑态来源优先，
  // 弹窗可能被二次切文档覆盖成中间文档——回退目标必须是编辑内容真正所属的 A）
  const handleDialogCancel = () => {
    const dlg = unsavedDialog
    if (!dlg) return
    setUnsavedDialog(null)
    // ⚠️ 「取消」= 什么都没发生：丢弃待提交的标签意图即可。
    // **不需要任何回滚**——标签组自始至终没被动过，这正是「延后提交」换来的性质
    // （若当初是「先改标签组、取消时再快照回滚」，这里就得精确还原被替换的预览槽）。
    useStore.getState().cancelTabChange()
    const backDocId = (dlg.type === 'doc' && (editing?.docId || dlg.prevDocId)) || undefined
    if (backDocId) {
      skipDocLoadRef.current = true
      useStore.getState().selectDoc(backDocId)
    }
  }

  // beforeunload：编辑中且有未保存修改时拦截关页（Tauri 关窗为边缘情况，见计划已知限制）
  useEffect(() => {
    if (!editing || !editDirty) return
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [editing, editDirty])

  // 编辑中未保存 → 同步 store 标记（GenPanel 重新生成入口可感知；无跨组件状态提升）
  useEffect(() => {
    setDirtyEditingDocId(editing && editDirty ? editing.docId : null)
  }, [editing, editDirty, setDirtyEditingDocId])

  // 外部清空标记（GenPanel 重新生成「放弃编辑继续」）→ 本地仍在编辑 → 自关闭编辑态，
  // 防生成完成后编辑框还开着旧值、保存覆盖新产物（评审遗留①）。
  // 本地自己清（保存/取消/切文档）时 editing 已先置 null，此分支不命中。
  useEffect(() => {
    if (!dirtyEditingDocId && editing && editDirty) {
      setEditing(null)
      setEditDirty(false)
    }
  }, [dirtyEditingDocId])

  const loadVersion = (version: number, mode: string) => {
    const docId = useStore.getState().currentDocId
    if (!docId || versionLoading) return
    setVersionLoading(true)
    const mdMode: 'notes' | 'polished' = mode === 'polished' ? 'polished' : 'notes'
    // ⚠️ 归属守卫必须带 **version 这一维**：只比 docId 时，「连点 v1 再点 v2」两个
    // 并发请求中先发的那个若后返回，会用 v1 的内容覆盖 v2（`versionLoading` 只是
    // 异步生效的按钮禁用，拦不住已经发出去的请求）。模式 #38：判据要能区分它该区分的事。
    versionReqRef.current = { docId, mode: mdMode, version }
    const isStale = () => {
      const req = versionReqRef.current
      return !req || req.docId !== docId || req.mode !== mdMode || req.version !== version
    }
    getMarkdown(docId, mdMode, version > 0 ? version : undefined).then(md => {
      if (!md || docId !== useStore.getState().currentDocId || isStale()) return
      if (mdMode === 'polished') {
        setCurrentPolishedContent(md.content)
      } else {
        setCurrentContent(md.content)
      }
      // 用户刚在这个 tab 切了版本 → 该 mode 的真相与当前视图**同时**更新
      const meta = toMarkdownMeta(md)
      perModeMetaRef.current[mdMode] = { meta, versions: md.versions || [] }
      setMarkdownMeta(meta)
      setMarkdownVersions(md.versions || [])
    }).catch(err => {
      // P1-16：版本加载失败不再静默——toast 告知（含瞬时网络故障归一化后的中文消息）
      console.error('加载版本失败:', err)
      useStore.getState().addToast(getErrorMessage(err), 'error')
    }).finally(() => {
      // 被取代的请求不解除 loading —— 解除权属于**最新**那次请求（否则旧请求先回来
      // 就把按钮放开了，用户可以在新请求还在飞的时候再点一次）
      if (!isStale()) setVersionLoading(false)
    })
  }

  /**
   * 加载文档内容（选中文档 effect 的公共逻辑，抽出供「未保存离开」确认后放行复用）。
   * docId 为 null → 仅清理旧内容（无选中文档）。
   */
  const loadDocContent = useCallback((docId: string | null) => {
    // ⚠️ 提交标签变更必须在这一行（**首行**），不能挪到 `if (!docId) return` 之后：
    // 「关闭最后一个标签」会走到 docId = null，那时同样要提交 pendingClose。
    // 另外 docId 为空串（GenPanel 有一处 `selectDoc('')`）时归一成 null，不追加标签。
    // 这一步同时是「未被守卫拦下」的证明——守卫命中时根本不会走到 loadDocContent。
    useStore.getState().commitTabChange(docId || null)
    // 切换文档时清除所有旧内容
    setCurrentContent(null)
    setCurrentPolishedContent(null)
    setCurrentRawText(null)
    setMarkdownMeta(null)
    setMarkdownVersions([])
    setContentLoadError(false)
    // 每个 mode 的产物真相随文档生命周期清零 —— 不清就会串到下一篇文档
    // （切文档后 activeView 未变 → 同步 effect 不重跑 → 读到上一篇的 meta/版本列表）
    perModeMetaRef.current = {}
    versionReqRef.current = null
    // 编辑状态按文档生命周期重置 + 跨文档隔离 edited 标记（防 A 的标记误判 B 的覆盖确认）
    setEditing(null)
    setEditDirty(false)
    useStore.getState().setMarkdownEdited('notes', false)
    useStore.getState().setMarkdownEdited('polished', false)
    if (!docId) return
    const capturedId = docId
    const isSame = () => capturedId === useStore.getState().currentDocId
    // recoverable 错误（"尚未生成"等）是预期状态，静默处理
    const isRecoverable = (err: unknown) => err instanceof ApiErrorException && err.apiError.recoverable
    const handleLoadError = (label: string, err: unknown) => {
      if (!isRecoverable(err)) { console.error(`${label}:`, err); if (isSame()) setContentLoadError(true) }
    }
    // ── 产物位门控：没产物就**一个请求都不发**（判据见 lib/artifactModes.ts）──
    // `modes` 由列表端点随 `index.json` 一起带出（零额外读盘）。此前无论有没有产物
    // 都盲发 4 条 `?mode=` 请求，没生成过就是一片 404 —— 前端确实把它们静默掉了
    // （不进 console.error、不弹 toast），但**浏览器网络层照打红字**（WebView 里 JS
    // 抑制不掉，模式 #49），而且协议层把「尚未生成」这个**正常状态**建模成了 HTTP 错误。
    const { notes: wantNotes, polished: wantPolished } = planArtifactRequests(
      useStore.getState().docs.find(d => d.id === capturedId)?.modes,
    )

    // 预加载 rawText（已无 base64，体积小，不阻塞）
    getTranscript(capturedId).then(tx => {
      if (tx && isSame()) setCurrentRawText(tx.rawText ?? '')
    }).catch(err => { handleLoadError('加载转录失败', err) })
    // 两个产物各一条请求（**没有的那个直接不发**），返回值汇给下面的 tab 判定。
    // 此前是「notes 一条 + polished 一条 + Promise.all 再两条」，其中
    // `?mode=polished` 与上一行**完全重复**，`无 mode` 那条只为了算 `hasContent`
    // ——同一份内容取两遍。现在请求数 = 有产物的档数（0/1/2）。
    const notesP = wantNotes
      ? getMarkdown(capturedId, 'notes').then(md => {
          if (!isSame()) return null
          // F4：生成中切回（生成快结束时）→ 跳过**赋值**——此 GET 可能返回保存前的旧版本 V0，
          // 网络慢时会在 onDone 的 refreshSaved(V1) 之后 resolve → V0 覆盖 V1。
          // 跳过安全：流式 buffer 会遮蔽 store 值。
          // ⚠️ 早退必须**把 md 交出去**：下面 `hasContent` 靠它判断，返回 undefined 会让
          // 「有产物 → 切到该类型的默认 tab」在生成中切回时误判成无产物、掉到转录 tab。
          if (useStore.getState().processingTasks[capturedId + '__generate']) return md
          if (md) {
            setCurrentContent(md.content)
            const meta = toMarkdownMeta(md)
            const versions = md.versions || []
            perModeMetaRef.current.notes = { meta, versions }
            // ⚠️ 只在**当前 tab 就是笔记**时直接更新视图值：文档加载是异步的，
            // 此刻 activeView 可能仍是上一轮的「整理版」——无条件写会让整理版 tab
            // 短暂挂上笔记的版本列表。切 tab 的正确同步由 effect 负责。
            if (useStore.getState().activeView !== 'polished') {
              setMarkdownMeta(meta)
              setMarkdownVersions(versions)
            }
          } else {
            delete perModeMetaRef.current.notes
            setCurrentContent(null)
            if (useStore.getState().activeView !== 'polished') {
              setMarkdownMeta(null)
              setMarkdownVersions([])
            }
          }
          useStore.getState().setMarkdownEdited('notes', !!md?.edited)
          return md
        }).catch(err => { handleLoadError('加载笔记失败', err); return null })
      : Promise.resolve(null)
    const polishedP = wantPolished
      ? getMarkdown(capturedId, 'polished').then(md => {
          if (!isSame()) return null
          // F4：同上——生成中跳过赋值，但值仍要传给 tab 判定
          if (useStore.getState().processingTasks[capturedId + '__generate']) return md
          // 整理版此前**从不更新** markdownMeta / markdownVersions（改动前此处只设内容）——
          // 于是切到整理版 tab 看到的是笔记的版本列表，且「就地覆盖」会拿笔记的版号去改
          // 整理版那一版（P1）。现在按 mode 分桶记下来，切 tab 时同步（见 perModeMetaRef）。
          const polMeta = md && md.content ? toMarkdownMeta(md) : null
          const polVersions = md?.versions || []
          if (polMeta) perModeMetaRef.current.polished = { meta: polMeta, versions: polVersions }
          else delete perModeMetaRef.current.polished
          setCurrentPolishedContent(md && md.content ? md.content : null)
          if (useStore.getState().activeView === 'polished') {
            setMarkdownMeta(polMeta)
            setMarkdownVersions(polVersions)
          }
          useStore.getState().setMarkdownEdited('polished', !!md?.edited)
          return md
        }).catch(err => { handleLoadError('加载整理版原文失败', err); return null })
      : Promise.resolve(null)
    // 文档/视频/PDF — 若无笔记则自动显示转录
    Promise.all([notesP, polishedP]).then(([notes, polished]) => {
      if (!isSame()) return
      const hasContent = !!(notes || polished?.content)
      const st = useStore.getState().docs.find(d => d.id === capturedId)?.sourceType || ''
      // 已有生成内容时，按文档类型选最佳默认 Tab（由 TabDef.defaultFor 驱动）
      if (hasContent) {
        const defKey = TabRegistry.defaultFor(st)
        if (defKey) setActiveView(defKey)
      } else if (['document', 'pdf', 'docx', 'pptx', 'xlsx', 'epub', 'video', 'composite'].includes(st)) {
        // 无产物时默认转录。⚠️ composite 走这支是**刻意的**：合成文档刚建好、笔记还没生成，
        // 用户此刻最该看到的是「## 来源文档」清单（选了哪几篇、哪几篇被压缩）
        setActiveView('transcript')  // epub 无产物时默认转录浏览（章节视图），骨架面板已砍
      }
    }).catch(err => { handleLoadError('加载文档内容失败', err) })
    // 加载已持久化的处理结果
    fetchProcessResults(capturedId).then(results => {
      if (results.length > 0 && isSame()) {
        loadImageResults(capturedId, results.map(r => ({
          mode: r.mode, text: r.content, engine: r.engine, id: r.id,
        })))
      }
    }).catch(err => {
      // P2-3：处理结果加载失败不再静默——图片 tab 会误显示「点击处理卡片」，toast 告知
      console.error('加载处理结果失败:', err)
      useStore.getState().addToast(getErrorMessage(err), 'error')
    })
  }, [setCurrentContent, setCurrentPolishedContent, setCurrentRawText, setMarkdownMeta, setMarkdownVersions, setContentLoadError, setActiveView, loadImageResults])

  // 选中文档时加载内容
  useEffect(() => {
    const prevId = prevDocIdRef.current
    prevDocIdRef.current = currentDocId
    // 弹窗「取消」回退：跳过本次清理+加载，保留原文档编辑态与未保存内容
    if (skipDocLoadRef.current) {
      skipDocLoadRef.current = false
      return
    }
    // 切文档防丢：编辑中且有未保存修改 → 三态弹窗（保存并离开 / 不保存 / 取消）。
    // prevDocId 用编辑态来源（editing.docId）而非 prevId——弹窗打开期间若再经
    // AI 浮窗切文档，prevId 会变成中间文档，用 prevId 会把编辑内容错存/错回退。
    if (prevId && prevId !== currentDocId && editing && editDirty) {
      setUnsavedDialog({ type: 'doc', target: currentDocId ?? undefined, prevDocId: editing.docId })
      return
    }
    loadDocContent(currentDocId)
  }, [currentDocId, loadDocContent])

  const sourceType = currentDoc?.sourceType || 'webpage'
  const typeMeta = getTypeMeta(sourceType)

  // 从 TabRegistry 获取当前 sourceType 下的可见 tab 列表
  const tabs = useMemo(() => {
    return TabRegistry.forSourceType(sourceType, {
      currentContent,
      imageResults,
    })
  }, [sourceType, currentContent, imageResults])

  // 当前 activeView 对应的 TabDef
  const activeTabDef: TabDef | undefined = TabRegistry.get(activeView)

  // 切换文档时，如果当前 activeView 不在新文档的 Tab 列表中，回退到第一个 Tab
  useEffect(() => {
    const validKeys = tabs.map(t => t.key)
    if (!validKeys.includes(activeView)) {
      setActiveView(validKeys[0])
    }
  }, [currentDocId, sourceType])

  // 当前显示的内容 — 由 TabDef.contentSource 决定从哪个字段读（lib/contentSources.ts）
  // 流式生成中 → notes/polished 优先读缓冲区（Map 由 streamTick 驱动重渲染）；否则显示已保存内容
  // 注：非 notes/polished tab 不再读 notes 缓冲（原硬编码 bug 语义，transcript 等
  // 在生成期间应显示各自内容源——有意的修正，勿"恢复"）
  const displayContent = activeTabDef
    ? getContentForView(activeView, {
        currentDocId, activeView,
        currentContent, currentPolishedContent, currentRawText,
        imageResults,
        readStreamBuffer: (mode) => {
          if (!isStreaming || !currentDocId) return null
          const b = getStreamBuffer(currentDocId + '__generate_' + mode)
          return b || null    // '' → null，触发 getter 的 ?? 回退到已保存内容
        },
      })
    : currentContent
  const processingTasks = useStore(s => s.processingTasks)
  // key 格式为 `${docId}__${kind}`，收集当前文档所有活跃任务
  const activeTasks = currentDocId
    ? Object.entries(processingTasks)
        .filter(([k]) => k.startsWith(currentDocId + '__'))
        .map(([, v]) => v)
    : (Object.entries(processingTasks)
        .filter(([k, v]) => k.startsWith('__import__') && v.kind === 'import')
        .map(([, v]) => v))

  /** 判断某个 view tab 是否正在处理中（从 TabDef.processingKinds 推导） */
  const isViewProcessing = (view: string) => {
    const def = TabRegistry.get(view)
    if (!def?.processingKinds) return false
    return activeTasks.some(task => {
      // process kind 需要额外匹配 mode（图片处理 tab）
      if (task.kind === 'process') return def.processingKinds!.includes('process') && view === task.mode
      return def.processingKinds!.includes(task.kind)
    })
  }

  /** 与当前 tab 匹配的活跃任务（多任务并发时占位显示最相关的那个，
   *  而不是 activeTasks[0]——generate + process 同时跑时会选错） */
  const taskForView = (view: string): TaskInfo | undefined => {
    const def = TabRegistry.get(view)
    if (!def?.processingKinds) return undefined
    return activeTasks.find(task => {
      if (task.kind === 'process') return def.processingKinds!.includes('process') && view === task.mode
      return def.processingKinds!.includes(task.kind)
    })
  }

  const renderContent = () => {
    if (!currentDocId || !currentDoc) {
      return (
        <div className="mx-auto px-10 py-16 text-center">
          <Package size={50} className="text-disabled mb-6 mx-auto opacity-20" />
          <h1 className="text-[30px] font-semibold mb-1">欢迎使用 LiMo</h1>
          <p className="text-[13px] text-disabled mb-8">在左侧面板导入内容，开始你的知识管理</p>

          {/* 欢迎文档 = 示例文档：笔记 / 整理版原文 / 转录 三产物形态（tab 样式对齐 PreviewArea 工具栏） */}
          <div className="mt-2 mb-6 inline-flex items-center gap-1 bg-surface rounded-lg p-0.5 border border-border-light">
            {WELCOME_TABS.map(t => (
              <button key={t.key}
                onClick={() => setWelcomeTab(t.key)}
                className={`px-3 py-1.5 rounded-md text-[12px] transition-all ${welcomeTab === t.key ? 'bg-elevated font-medium shadow-sm' : 'text-dimmed hover:text-foreground'}`}
              >{t.label}</button>
            ))}
          </div>

          <div className="markdown-body max-w-160 mx-auto text-left" data-texture-reset="true">
            <ReactMarkdown remarkPlugins={REMARK_PLUGINS} rehypePlugins={REHYPE_PLUGINS}>
              {welcomeTab === 'notes' ? WELCOME_NOTES : welcomeTab === 'polished' ? WELCOME_POLISHED : WELCOME_TRANSCRIPT}
            </ReactMarkdown>
          </div>
        </div>
      )
    }
    switch (activeView) {
      // Excel 数据预览 — 直接用 Markdown 渲染表格
      case 'data_preview':
        if (isViewProcessing(activeView) && !isStreaming) return <ProcessingPlaceholder task={taskForView(activeView)} />
        if (!currentRawText) {
          return <EmptyState title="数据预览不可用" description="请重新转录后再试" />
        }
        return <TranscriptView rawText={currentRawText} sourceType={currentDoc?.sourceType} />
      case 'polished':
      case 'summary':
        // placeholder 按当前 tab 定位产物（并行双产物时 task.mode 会来回跳）
        if (isViewProcessing(activeView) && !isStreaming) return <ProcessingPlaceholder task={taskForView(activeView)} mode={activeView === 'polished' ? 'polished' : 'notes'} />
        if (typeMeta.isExcel) {
          // Excel: 仅数据摘要（分析报告已移除）——用 displayContent（含流式 buffer 回退，
          // F6：生成中切走再切回能显示流式内容，而非直读 currentContent 的空态"尚未生成"）
          if (!displayContent) {
            return <EmptyState title="尚未生成" description="点击左侧「生成数据摘要」按钮" />
          }
          return <>
            <MarkdownPreview sourceUrl={sourceUrl} isVideo={isVideo} content={displayContent} meta={markdownMeta} />
          </>
        }
        if (!displayContent) {
          if (isStreaming) return <ProcessingPlaceholder task={taskForView(activeView)} mode={activeView === 'polished' ? 'polished' : 'notes'} />
          if (contentLoadError) {
            return <EmptyState icon={<AlertTriangle size={16} className="text-warning" />} title="内容加载失败" description="请检查后端是否正常运行，然后刷新页面重试" action={<button onClick={() => window.location.reload()} className="px-4 py-1.5 bg-accent text-on-accent text-[12px] rounded-full hover:bg-accent-hover">刷新页面</button>} />
          }
          return <EmptyState title="尚未生成" description="点击左侧「生成」按钮为本文档生成 Markdown 笔记" />
        }
        // 编辑模式判定：summary tab 编辑 notes；polished tab 编辑 polished
        const editMode = activeView === 'polished' ? 'polished' : 'notes'
        const inEditing = editing?.mode === editMode
        return <>
          {/* P2-1：继续生成入口——通知 GenPanel 以 continue_from=partial 续写残缺产物 */}
          <PartialBanner versions={markdownVersions} isStreaming={isStreaming}
            onContinue={() => window.dispatchEvent(new CustomEvent('limo:continue-generate'))} />
          {inEditing ? (
            <TextareaMarkdownEditor
              value={editValue}
              onChange={(v) => { setEditValue(v); setEditDirty(v !== displayContent) }}
              onSave={handleSaveEdit}
              onCancel={cancelEditing}
              dirty={editDirty}
              saving={savingEdit}
              sourceUrl={sourceUrl}
              isVideo={isVideo}
            />
          ) : (
            <>
              {/* 产物自证条：这一版是哪一版 / 什么时候生成 / 多少字 / 谁生成的。
                  ⚠️ 只挂在**只读态**——编辑态下用户在改正文，顶上压一条元数据只是干扰。
                  ⚠️ 此刻 `markdownMeta` 已是**本 tab 的 mode**（切 tab 由
                  `perModeMetaRef` 的同步 effect 换成那个 mode 自己的，见其注释），
                  故不需要在这里再校验 mode。
                  ⚠️ 流式生成中**不显示**：这时 `markdownMeta` 还是**上一版**的数
                  （store 只在生成完成时被更新），配着新流入的正文显示是错位读数。 */}
              {!isStreaming && (
                <div className="mb-2 px-1 flex flex-wrap items-center gap-x-3 gap-y-1.5">
                  <ArtifactMetaBar meta={markdownMeta} versions={markdownVersions} />
                  {/* 标签建议：库里已有标签 ∩ 这篇正文里出现过的。点一下直接加。
                      ⚠️ 与自证条同排、同款小徽章 —— 它是「顺手可做的一件事」，
                      不该单占一行把正文推下去。无建议时整个组件返回 null。 */}
                  <TagSuggestions content={displayContent} />
                </div>
              )}
              {/* 内容区工具条：已编辑徽章 + 编辑按钮（Excel 数据摘要走 summary 内容源但不可编辑，方案范围表） */}
              <div className="flex items-center justify-end gap-2 mb-1 px-1 -mt-0.5">
                {markdownEdited[editMode] && (
                  <span className="text-[11px] text-warning flex items-center gap-1"><Pencil size={11} /> 已手动编辑</span>
                )}
                <button
                  onClick={startEditing}
                  disabled={isStreaming || isViewProcessing(activeView)}
                  className="px-2.5 py-1 text-[11px] text-dimmed hover:text-foreground hover:bg-surface-hover rounded-md transition-colors disabled:opacity-40 disabled:pointer-events-none flex items-center gap-1"
                  title="编辑产物（源码 + 分屏预览）"
                ><Pencil size={12} /> 编辑</button>
              </div>
              {/* 骨架轴分派 —— **唯一接入点**。两条分支都过 `MarkerContent`（语义标记
                  与骨架轴正交：`paper` 下强调块在正文流里，`card` 下它在卡内）。
                  ⚠️ `paper` 且**无标记**时 `MarkerContent` 走快路径，渲染树与改造前逐字
                  相同（同组件、同 props、同顺序）—— 这是可断言的事实，见
                  `test/cardSections.test.tsx`。无 H2 时 CardSections **内部回落**到与
                  本行完全相同的路径。 */}
              {skeleton === 'card'
                ? <CardSections sourceUrl={sourceUrl} isVideo={isVideo} content={displayContent} meta={markdownMeta} />
                : <MarkerContent sourceUrl={sourceUrl} isVideo={isVideo} content={displayContent} meta={markdownMeta} />}
              {/* 追问卡片 —— 生成时勾了「追问卡片」才有的 4~6 个可点问题（点了带这篇
                  文档直接问 AI）。它是**后端独立补调一次**产出的，住在旁路文件里，
                  **不在正文、不在导出**，故这里只是把它挂到笔记末尾。

                  ⚠️ **判据走 `TabRegistry.htmlMode`（= 'notes'），不是 `activeView` 字面量**：
                  本文件别处已踩过这个坑（笔记 tab 的 key 未必叫 `summary`）。用 `htmlMode`
                  同时也把 Excel 数据摘要那支挡在外面 —— 它走 `summary` 内容源但
                  `htmlMode !== 'notes'`，而后端同样只给 notes 产追问，**两侧判据同源**。
                  ⚠️ **流式生成中不渲染**：此刻 `markdownMeta` 还是上一版，配新流入的正文
                  是错位读数（与上面的 `ArtifactMetaBar` 同款理由）。
                  ⚠️ **编辑态不渲染**：用户在改正文，末尾挂一排 chip 只是干扰。 */}
              {!inEditing && !isStreaming
                && TabRegistry.get(activeView)?.htmlMode === 'notes'
                && !!markdownMeta?.followUps?.length && (
                <div className="mt-5 pt-3 border-t border-border-light px-1">
                  <ChatFollowUpList
                    items={markdownMeta.followUps}
                    streaming={chatStreaming}
                    onSelect={handleFollowUpSelect}
                  />
                </div>
              )}
            </>
          )}
        </>

      case 'ocr':
        if (isViewProcessing(activeView)) return <ProcessingPlaceholder task={taskForView(activeView)} />
        // 图片默认 OCR 文字（尚未处理时显示引导）
        return (
          <EmptyState
            icon={<Image size={50} className="text-disabled mx-auto opacity-20" />}
            title={<h2 className="text-[18px] font-semibold">{currentDoc.title}</h2>}
            description="图片已导入，选择一个处理模式开始提取"
            action={<div className="flex items-center justify-center gap-3 text-[12px] text-muted">
              {IMAGE_MODES.map(m => {
                const Icon = IMAGE_MODE_ICONS[m]
                return <span key={m} className="flex items-center gap-1"><Icon size={14} /> {IMAGE_MODE_LABELS[m]}</span>
              })}
            </div>}
          />
        )

      case 'original':
        if (isViewProcessing(activeView)) return <ProcessingPlaceholder task={taskForView(activeView)} />
        // 非图片类型不应该看到此 tab（切换文档时 activeView 可能滞后一帧）
        if (!typeMeta.isImage) return null
        return (
          <div className="flex-1 flex items-center justify-center bg-surface-hover p-6">
            <Zoom>
              <img
                src={apiUrl(`/assets/${currentDocId}/original.png`)}
                alt={currentDoc?.title || '原图'}
                className="max-w-full max-h-[calc(100vh-140px)] object-contain rounded-xl shadow-lg cursor-zoom-in"
              />
            </Zoom>
          </div>
        )

      case 'extract':
      case 'table':
      case 'digitize':
      case 'understand': {
        if (!typeMeta.isImage) return null
        // 标签来自 `lib/imageModes`（单一真相源）
        const modeLabels = IMAGE_MODE_LABELS
        const result = imageResults[currentDocId || '']?.[activeView]
        // 全部用 Markdown 渲染：VL 模型可能在任意模式下输出表格/公式

        if (isViewProcessing(activeView)) return <ProcessingPlaceholder task={taskForView(activeView)} />
        if (!result) {
          const modeMeta: Record<string, { icon: React.ReactNode; desc: string }> = {
            extract: { icon: <ScanText size={40} />, desc: '提取图片中的全部文字，保留段落结构' },
            table: { icon: <Table size={40} />, desc: '优先识别表格数据，输出 Markdown 表格' },
            digitize: { icon: <ScanLine size={40} />, desc: '将文档扫描件转为可编辑文本' },
            understand: { icon: <Eye size={40} />, desc: '描述并解读图片中的视觉信息' },
          }
          const meta = modeMeta[activeView] || { icon: <FileText size={40} />, desc: '' }
          return (
            <EmptyState
              icon={<div className="text-5xl opacity-20">{meta.icon}</div>}
              title={<h2 className="text-[18px] font-semibold">{modeLabels[activeView] || activeView}</h2>}
              description={meta.desc}
              hint="点击左侧处理卡片开始"
            />
          )
        }

        return (
          <ImageResultView
            result={result}
            mode={activeView}
            title={currentDoc?.title}
            createdAt={currentDoc?.createdAt}
            markdownComponents={imageMarkdownComponents}
          />
        )
      }

      case 'transcript':
        if (isViewProcessing(activeView)) return <ProcessingPlaceholder task={taskForView(activeView)} />
        // 文档类转录（PDF/Word等）→ 统一用 MarkdownPreview 渲染
        if (currentDoc?.sourceType && !['video', 'image', 'webpage', 'text'].includes(currentDoc.sourceType) && currentRawText) {
          let isJson = false
          try { JSON.parse(currentRawText); isJson = true } catch {}
          if (!isJson) {
            return <MarkdownPreview content={currentRawText} meta={markdownMeta} sourceUrl={sourceUrl} isVideo={false} />
          }
        }
        return (
          <div className="mx-auto px-10 py-8">
            {currentRawText ? (
              <>
                <TranscriptView rawText={currentRawText} sourceType={currentDoc?.sourceType} />
                {/* 转录质量指示 */}
                <TranscriptQualityNote rawText={currentRawText} sourceType={currentDoc?.sourceType} />
              </>
            ) : (
              <p className="text-[13px] text-disabled">加载中...</p>
            )}
          </div>
        )

      case 'mindmap':
        // 思维导图只依赖笔记内容——笔记已有内容（含流式 partial）就直接渲染，
        // 不被"正在生成中"拦截（串行生成中 notes 已完成但 polished 还在跑，
        // __generate 任务未结束 → isViewProcessing 会误拦）
        if (!currentContent) {
          if (isViewProcessing(activeView)) return <ProcessingPlaceholder task={taskForView(activeView)} />
          return <EmptyState title="暂无可视化内容" description="点击左侧「生成」按钮为本文档生成 Markdown 笔记后，可在此查看思维导图" />
        }
        return <div id="mindmap" className="relative flex-1 min-h-0"><MindmapView value={currentContent} /></div>

      default:
        return null
    }
  }

  return (
    <>
    <main className="flex-1 flex flex-col overflow-hidden bg-surface rounded-2xl">
      {/* 文档标签栏 —— 与工具栏一起隐藏：沉浸会同时藏掉文档库，没有打开新文档的入口时
          一排标签只是噪音。`docTabs` 为空（欢迎页）时它自己返回 null。 */}
      {!immersive && <DocTabBar />}
      {/* 工具栏 — 对齐 BiliNote MarkdownHeader。
          沉浸下整条不渲染：tab/复制/导出/宽度/沉浸/日志全是**操作入口**，属去噪目标
          （「沉浸」自己也在其中 —— 它的进入靠这条工具栏，退出靠右上角常驻角标）。
          ⚠️ 这里**刻意没有 AI 按钮** —— 左侧活动栏的「AI 对话」是唯一入口（同一个
          `toggleAI`）。曾两处都有，同屏两个 MessageSquare 图标，用户只会觉得
          「这图标是什么」。（2026-10-10 前那句话写的是「顶栏 AI 是唯一入口」——
          入口已从顶栏迁到活动栏，判据不变、位置变了。）
          ⚠️ 它不在插槽系统里（`hideSlots` 管不到），必须显式控制。
          ⚠️ TOC **刻意保留** —— 它是阅读导航（长文跳章），与「操作噪音」性质不同，
          且本就能折叠成 28px 竖条，空间成本由用户自己控制。隐藏它 = 功能损失而非去噪。 */}
      {!immersive && <div className="flex items-center justify-between px-6 py-2.5 border-b border-border-light shrink-0">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1 bg-surface rounded-lg p-0.5">
            {tabs.map(t => (
              <button key={t.key}
                onClick={() => handleTabSwitch(t.key)}
                className={`px-3 py-1.5 rounded-md text-[12px] transition-all ${activeView === t.key ? 'bg-elevated font-medium shadow-sm' : 'text-dimmed hover:text-foreground'}`}
              >{t.label}</button>
            ))}
          </div>
          {/* 版本切换器 — 由 TabDef.showVersionSwitcher 驱动；编辑中隐藏（编辑态与切版本冲突） */}
          {activeTabDef?.showVersionSwitcher && !editing && (
            <VersionSwitcher
              mode={activeTabDef.showVersionSwitcher}
              versions={markdownVersions || []}
              activeMeta={markdownMeta}
              loading={versionLoading || isStreaming}
              onSelect={(v) => loadVersion(v, activeTabDef.showVersionSwitcher!)}
            />
          )}
        </div>
        <div className="flex items-center gap-1.5">
          <CopyContentBtn ctx={{
            currentDocId, activeView,
            currentContent, currentPolishedContent, currentRawText,
            imageResults,
          }} />
          <ExportMenu />
          {/* 阅读宽度切换 */}
          <button
            onClick={cycleContentWidth}
            className="w-7 h-7 flex items-center justify-center text-dimmed hover:bg-surface-hover rounded-lg transition-colors"
            title={`阅读宽度：${WIDTH_RATIO[contentWidth].label}（Ctrl+W）`}
          >
            {contentWidth === 'narrow' ? <AlignCenter size={15} /> : contentWidth === 'wide' ? <Maximize2 size={15} /> : <Columns size={15} />}
          </button>
          {/* 产物骨架切换 —— 「章节卡片」的一键开关。
              ⚠️ 2026-10-11 起它只回答**「要不要按标记显示卡片」**：哪几节成卡由模型在
              产物里标（`limo-block:v1 type="card"` 写在标题之下），未标记的章节在两种
              骨架下长得一样。
              ⚠️ 门控与正文里的骨架分派**同源**：只有 notes/polished 的非 Excel 路径会
              渲染骨架（见下方 `skeleton === 'card'` 分派），其余 tab（数据预览/转录/
              思维导图/图片/Excel 数据摘要）切了没有任何效果 —— 那种只在**设置页**能调
              是可以接受的，摆在正文工具栏上就成了「调了没反应的死旋钮」（模式 #53）。
              ⚠️ 高亮语义与右侧装饰开关一致：**高亮 = 已改变默认**（默认 `paper`）。
              这里只切开关；8 套视觉方案在 设置 → 阅读与产物 里选。 */}
          {activeTabDef?.isStandardMarkdown && !typeMeta.isExcel && (
            <button
              onClick={() => setCard({ skeleton: skeleton === 'card' ? 'paper' : 'card' })}
              className={`w-7 h-7 flex items-center justify-center rounded-lg transition-colors ${skeleton === 'card' ? 'bg-accent/10 text-accent' : 'text-dimmed hover:bg-surface-hover'}`}
              title={skeleton === 'card' ? '章节卡片：已开启（模型标为重点的章节显示为卡片，点击还原为连续正文）' : '章节卡片：已关闭（点击按标记显示卡片）'}
            >
              <LayoutGrid size={15} />
            </button>
          )}
          {/* 内容块装饰开关 —— 只影响「标记块产生的附加物」（边框/淡底/居中），
              **内容一字不减**。⚠️ 2026-10-11 起射程含内容卡片族（表格/代码/图片），
              关掉后它们**还原成没包过围栏的样子**；见 `lib/marker-prefs.ts` 文件头。
              ⚠️ 高亮表示「已改变默认」（装饰被关掉），与右侧日志按钮的按下态同一套
              表达；默认态不高亮，避免常驻视觉噪音。
              ⚠️ 本工具栏在沉浸态整条不渲染 —— 本轮接受这个边界，不在沉浸态另开入口
              （设置页里有同一个开关，见 SettingsPage 的「阅读与产物」区）。 */}
          <button
            onClick={() => setMarker({ decor: !markerDecor })}
            className={`w-7 h-7 flex items-center justify-center rounded-lg transition-colors ${markerDecor ? 'text-dimmed hover:bg-surface-hover' : 'bg-accent/10 text-accent'}`}
            title={markerDecor ? '内容块装饰：显示中（点击隐藏装饰）' : '内容块装饰：已隐藏（点击显示）'}
          >
            <Highlighter size={15} />
          </button>
          {/* 沉浸阅读 —— 与 Ctrl+\ 共用同一个 `toggleImmersive`（它带 processingTasks
              守卫：有任务在跑时拒绝进入并 toast，否则 GenPanel 卸载会让任务变僵尸）。
              ⚠️ 这条按钮只在**非沉浸**时可见（整条工具栏在沉浸下不渲染），所以它实际
              只承担「进入」；「退出」由右上角常驻角标 / Esc / Ctrl+\ 承担 —— 三者与这里
              在 store 上收口于同一对 action。 */}
          <button
            onClick={toggleImmersive}
            className="w-7 h-7 flex items-center justify-center text-dimmed hover:bg-surface-hover rounded-lg transition-colors"
            title="沉浸阅读（Ctrl+\）"
          >
            <Expand size={15} />
          </button>
          <button
            onClick={() => setShowLogs(!showLogs)}
            className={`px-3 py-1.5 text-[12px] rounded-full transition-colors ${showLogs ? 'bg-accent/10 text-accent' : 'text-dimmed hover:bg-surface-hover'}`}
            title="系统日志"
          ><Terminal size={14} /></button>
        </div>
      </div>}

      {/* 内容 + TOC */}
      <div className="preview-area flex-1 flex overflow-hidden">
        {activeTabDef?.showTOC && (
          tocCollapsed ? (
            <div
              onClick={() => setSlot('toc', { collapsed: false })}
              className="flex flex-col items-center justify-center shrink-0 bg-elevated hover:bg-surface-hover transition-colors cursor-pointer border-r border-border-light"
              style={{ width: 28 }}
              title="展开目录"
            >
              <ChevronRight size={14} className="text-muted" />
              <span className="text-[9px] text-disabled mt-1 select-none" style={{ writingMode: 'vertical-rl' }}>目录</span>
            </div>
          ) : (
            <>
              <TOC width={tocWidth} />
              <ResizeHandle onResize={resizeToc} />
            </>
          )
        )}
        <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
          {/* VideoBanner — 窄条，由 TabDef.showBanner 驱动 */}
          {activeTabDef?.showBanner && currentDoc?.sourceType === 'video' && (
            <VideoBanner title={currentDoc.title}
              sourceUrl={currentDoc.sourceUrl} sourceType={currentDoc.sourceType} />
          )}
          <div
            ref={containerRef}
            className={`preview-content flex-1 flex flex-col ${activeView === 'mindmap' ? 'overflow-hidden' : 'overflow-y-scroll'} ${activeView === 'mindmap' ? '' : 'mx-auto w-full'} ${activeView === 'mindmap' ? '' : contentWidth === 'wide' ? 'px-1 md:px-2 lg:px-4 xl:px-6' : contentWidth === 'normal' ? 'px-3 md:px-6 lg:px-10 xl:px-16' : 'px-4 md:px-12 lg:px-20 xl:px-32'}`}
            style={activeView === 'mindmap' ? {} : { maxWidth: contentMaxW, fontSize: `${fontSizeScale[contentWidth]}em`, transition: 'max-width 350ms ease-out, padding 350ms ease-out, font-size 350ms ease-out' }}
          >
            {renderContent()}
          </div>
        </div>
      </div>
      {/* 退出沉浸 —— 常驻可见的逃生口。**刻意不做鼠标空闲隐藏**：那是惩罚性设计
          （用户想退出时找不到出口）。改用低透明度让位给正文，hover 变实。
          Esc / Ctrl+\ 是同一动作的另两个入口，三者在 store 上收口于 setImmersive。 */}
      {immersive && (
        <button
          onClick={() => useStore.getState().setImmersive(false)}
          className="fixed top-4 right-4 z-30 flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-surface/80 backdrop-blur-sm border border-border-light text-[12px] text-muted shadow-sm opacity-50 hover:opacity-100 hover:text-foreground hover:bg-elevated transition-all"
          title="退出沉浸阅读（Esc）"
        >
          <Minimize2 size={13} />
          <span>退出沉浸</span>
        </button>
      )}
    </main>
    {showLogs && <LogPanel onClose={() => setShowLogs(false)} />}
    {unsavedDialog && (
      <UnsavedChangesDialog
        saving={dialogSaving}
        onSave={handleDialogSave}
        onDiscard={handleDialogDiscard}
        onCancel={handleDialogCancel}
      />
    )}
    </>
  )
}
