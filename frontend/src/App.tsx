import { useEffect, useState, useCallback, useRef } from 'react'
import { useStore } from './store'
import { onConfigChanged } from './lib/configEvents'
import { Toast } from './components/ui/Toast'
import { TaskPanel, TaskPanelToggle } from './components/ui/TaskPanel'
import { ExportPanel } from './components/ui/ExportPanel'
import { FloatingAI } from './components/ai-chat/FloatingAI'
import { SettingsPage } from './components/settings/SettingsPage'
import { OnboardingWizard } from './components/ui/OnboardingWizard'
import { SlotLayout } from './components/ui/SlotLayout'
import { ActivityBar } from './components/ui/ActivityBar'
import { SearchBar } from './components/ui/SearchBar'
import { ThemeToggle } from './components/ui/ThemeToggle'
import { fetchCapabilities, listTranscripts, listFolders, getMarkdown, getTranscript } from './api/client'
import { startHealthPoll } from './lib/health'
import { toMarkdownMeta } from './lib/markdownMeta'
import {
  installWarningText, isInstallWarningDismissed, dismissInstallWarning,
  type InstallPathWarning,
} from './lib/installWarning'
import { BackendStartupFailure } from './components/ui/BackendStartupFailure'
import type { StartupFailure } from './lib/startupFailure'
import { Archive, X } from 'lucide-react'

// 冷启动静默期（ms）：后端实测 ~2s 就绪，这段窗口内**故意一条请求都不发** ——
// 起步就探测的话，那一刻后端必然还没监听，会稳定产生一条无意义的失败；而 WebView 里
// fetch 失败的 ERR_CONNECTION_REFUSED 是浏览器自动打印、JS 无法抑制的。
// 静默期内由 Rust 侧的 backend-ready 事件唤醒（Rust 的探测在独立进程，不碰网络栈），
// 只有「事件已错过」的场景（页面刷新时后端已在运行）才需要这次探测兜底。
const BACKEND_PROBE_DELAY_MS = 2500

// 硬兜底（ms）：万一 backend-ready 与 backend-startup-timeout 都没到达
// （窗口句柄取不到时 Rust 的 emit 会静默丢弃），也不能让加载屏永远停住。
const BACKEND_HARD_TIMEOUT_MS = 50_000

// 文件夹加载失败的提示只发一次（模块级而非组件级：effect 在开发模式会跑两遍，
// 且这只是降级能力——文档列表本身好着，重复弹窗比不弹更烦人）。
let folderLoadWarned = false

/**
 * 全局快捷键 —— 抽成**具名导出的纯函数**：它只依赖事件对象与 store 快照，不碰组件树。
 *
 * 抽出来的理由是**可测**：渲染整个 App 来验一次按键，会连带拉起 GenPanel / PreviewArea /
 * FloatingAI 及它们各自的网络请求，成本远高于这几行的价值；抽出来就能直接喂一个假事件，
 * 断言「键盘确实接在这个动作上」—— 而这正是最容易漏测的一环（store action 测过了，
 * 但没人验证按键绑没绑上去）。
 */
export function handleAppKeyDown(e: React.KeyboardEvent) {
  if (!(e.metaKey || e.ctrlKey)) return
  const s = useStore.getState()
  switch (e.key) {
    case 'g':
      e.preventDefault()
      // 操作进行中时拦截，防止面板卸载导致任务变僵尸
      if (Object.keys(s.processingTasks).length > 0) {
        s.addToast('操作进行中，请完成或取消后再切换面板', 'info')
      } else {
        // ⚠️ **语义随「侧栏布局」偏好而变**（2026-10-10 起 Ctrl+G 与活动栏图标共用同一个
        // `togglePanel`）：互斥（默认）下它从「切换生成面板开合」变成「切到生成面板」——
        // 面板没开就打开、开了就关闭，**关闭时不露出文档库**（对齐 VS Code：按的是「关掉
        // 这个」，不是「给我那个」）。并排模式下与改造前完全一致（各自独立收起/展开）。
        s.toggleGenPanel()
      }
      break
    case 'i': e.preventDefault(); s.toggleAI(); break
    case 'w': e.preventDefault(); s.cycleContentWidth(); break
    // 文档库搜索 —— 值得全局劫持：用户按 Ctrl+K 时想的是「去搜文档」，而不是
    // 「搜我此刻正停着的这个输入框」。故这里**不做** activeElement 守卫
    // （与 Ctrl+A 相反，那个必须让位给输入框内的全选）。
    case 'k': e.preventDefault(); s.openDocLibSearch(); break
    // 沉浸阅读 —— 用 Ctrl+\ 而**不是** Ctrl+Shift+F：后者的 e.key 在 Shift 下是大写
    // 'F'（下面这些 case 全是小写，写 'f' 永远不触发），且 Windows 上 Ctrl+Shift 是
    // 默认的输入法/键盘布局切换前缀，会被输入法吞掉。Ctrl+\ 与 LobeChat 的 Zen Mode 同键。
    // 裸 Esc 退出的监听不在这里 —— L41 的 Ctrl/Meta 守卫会把它直接吞掉，见下面的独立 effect。
    case '\\': e.preventDefault(); s.toggleImmersive(); break
    // 正文字号 —— 与预览区的 Ctrl+滚轮**共用同一对 store 动作**（档位换算与 toast
    // 都只此一份）。⚠️ `=` 与 `+` 必须都列：`+` 是 Shift+`=` 的 e.key，
    // 只写 `=` 的话「按住 Shift 放大」会静默失效；`-` 与 `_` 同理。
    // **不要用 `toLowerCase()` 兜**——`'+'` 小写之后还是 `'+'`，两者不通用。
    case '=': case '+': e.preventDefault(); s.stepFontScale(1); break
    case '-': case '_': e.preventDefault(); s.stepFontScale(-1); break
    case '0': e.preventDefault(); s.resetFontScale(); break
  }
}

export default function App() {
  const aiVisible = useStore(s => s.aiVisible)
  const setCapabilities = useStore(s => s.setCapabilities)
  const setDocs = useStore(s => s.setDocs)
  const setFolders = useStore(s => s.setFolders)
  const [showSettings, setShowSettings] = useState(false)
  const [showExport, setShowExport] = useState(false)
  // 安装路径告警（Rust `backend-warning`）—— 空串 = 无告警、整条不渲染
  const [installWarning, setInstallWarning] = useState('')
  // 后端启动失败诊断（Rust `backend-startup-failed`）—— 非空且进程已退出时停在失败屏
  const [startupFailure, setStartupFailure] = useState<StartupFailure | null>(null)
  // `initOnce` 活在启动 effect 的闭包里（`done` 标志保证只跑一次），
  // 失败屏的「仍然继续」够不到它 —— 用 ref 把同一个入口透出来，而不是在外层
  // 再写一份「设 ready + init」，那会绕开 `done` 让它有机会跑两次。
  const continueStartupRef = useRef<(() => void) | null>(null)
  const [showSetup, setShowSetup] = useState(() => {
    try { return !localStorage.getItem('limo_setup_done') } catch { return false }
  })
  const backendReady = useStore(s => s.backendReady)
  const setBackendReady = useStore(s => s.setBackendReady)
  const backendStatus = useStore(s => s.backendStatus)
  const immersive = useStore(s => s.immersive)

  // 沉浸阅读的**逃生口**。沉浸会隐藏所有可见出口（顶栏 / 预览工具条 / 两侧面板），
  // 快捷键万一失效用户就被困住 —— 所以 Esc 退出是硬需求，不是锦上添花。
  // ⚠️ 它**进不去** handleAppKeyDown：L41 的 `if (!(e.metaKey || e.ctrlKey)) return`
  // 会把裸 Esc 直接吞掉。故单开一个 document 监听，仅在沉浸期间挂载；用捕获阶段，
  // 抢在个别组件（如文档库的 Esc 清空选择集）消费掉之前。
  useEffect(() => {
    if (!immersive) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.preventDefault()
      useStore.getState().setImmersive(false)
    }
    document.addEventListener('keydown', onKey, true)
    return () => document.removeEventListener('keydown', onKey, true)
  }, [immersive])

  // 刷新 capabilities（引擎下拉/场景标签/文件类型的数据源）。
  // checkInterrupted：仅初始化时检查未完成生成标记，配置刷新不重复提示
  const refreshCapabilities = useCallback((checkInterrupted: boolean) => {
    fetchCapabilities().then(caps => {
      setCapabilities(caps)
      if (checkInterrupted) {
        // ⚠️ 暂时禁用：未完成生成提示对已完成文档误报过多（marker 残留误判）。
        // 后端 _scan_interrupted_generations 已加「产物已生成 → 自愈清理」，
        // 待确认无残留后再启用以下提示逻辑。
        // const interrupted = (caps as any).interruptedGenerations
        // if (Array.isArray(interrupted) && interrupted.length > 0) {
        //   useStore.getState().addToast(`检测到 ${interrupted.length} 个未完成的生成（上次应用中断），请重新生成`, 'info')
        // }
      }
    }).catch(err => {
      console.error('加载能力配置失败:', err)
      useStore.getState().addToast('加载能力配置失败，请检查后端是否运行', 'error')
    })
  }, [setCapabilities])

  // 运行期持续探活（P1.1）— 独立于启动探活（后者只管「未就绪加载屏」）：
  // 后端运行中异常断开 → backendStatus=down/crashed → 顶部横幅；恢复自动消失。
  // ⚠️ 必须等 backendReady 再启动：冷启动阶段后端还没起，tick 的首次请求必然失败，
  // 而那条失败会在控制台留下 ERR_CONNECTION_REFUSED（WebView 自动打印，JS 无法抑制）。
  useEffect(() => {
    if (!backendReady) return
    return startHealthPoll()
  }, [backendReady])

  useEffect(() => {
    // 订阅设置页配置变更 → 刷新 capabilities（保存后无需手动刷新页面）
    const unsubConfig = onConfigChanged(() => refreshCapabilities(false))

    const init = () => {
      refreshCapabilities(true)
      // ⚠️ 置位必须**早于**发请求：否则首帧渲染的是空 docs，列表会先显示一次
      // 「还没有文档」再被数据覆盖 —— 冷启动时用户真的能看到那句假空态。
      useStore.getState().setDocsLoading(true)
      // 文件夹与文档列表并行拉取（两者互不依赖）。
      // ⚠️ 文件夹失败不能拖垮文档列表——故单独 catch，退化为「没有文件夹」，
      // 而不是整块进 catch 后连文档都加载不出来。但**失败要可见**：静默吞掉会让
      // 「文档全挤在未分类里」看起来像数据损坏，而不是一次加载失败。
      listFolders().then(setFolders).catch(err => {
        console.error('加载文件夹失败:', err)
        if (!folderLoadWarned) {
          folderLoadWarned = true
          useStore.getState().addToast('加载文件夹失败，文档归类可能显示不完整', 'error')
        }
      })
      listTranscripts().then(docs => {
      setDocs(docs)
      // 恢复上次选中的文档和视图
      try {
        const lastId = localStorage.getItem('mbw_lastDocId')
        const lastView = localStorage.getItem('mbw_lastView')
        if (lastId && docs.some(d => d.id === lastId)) {
          useStore.getState().selectDoc(lastId)
          if (lastView) useStore.getState().setActiveView(lastView)
        }
      } catch { /* ignore */ }
      // 刷新后 processingTasks 丢失是预期的（SSE 连接已断开），
      // 但服务器上已生成的内容应立即可见——主动预加载
      try {
        const restoredId = useStore.getState().currentDocId
        if (restoredId) {
          getTranscript(restoredId).then(tx => {
            if (tx && useStore.getState().currentDocId === restoredId) {
              useStore.getState().setCurrentRawText(tx.rawText ?? '')
            }
          }).catch(() => {})
          getMarkdown(restoredId, 'notes').then(md => {
            if (md && useStore.getState().currentDocId === restoredId) {
              useStore.getState().setCurrentContent(md.content)
              useStore.getState().setMarkdownMeta(toMarkdownMeta(md))
              useStore.getState().setMarkdownVersions(md.versions || [])
            }
          }).catch(() => {})
          getMarkdown(restoredId, 'polished').then(md => {
            if (useStore.getState().currentDocId === restoredId) {
              useStore.getState().setCurrentPolishedContent(md?.content || null)
            }
          }).catch(() => {})
        }
      } catch { /* ignore */ }
    }).catch(err => {
      console.error('加载文档列表失败:', err)
      useStore.getState().addToast('加载文档列表失败，请检查后端是否运行', 'error')
    }).finally(() => {
      // 成败都要落位，否则失败时列表永远停在骨架屏上，比显示空态更像卡死
      useStore.getState().setDocsLoading(false)
    })
  }

    // Tauri 模式：以 Rust 的就绪事件为主，前端不再自己轮询 /health。
    //
    // 此前这里是「90 × 500ms」的主动轮询，它与 Rust 侧的探测（lib.rs 的
    // spawn_backend_ready_probe，500ms 间隔、45s 超时）完全重复，而代价极不对等：
    // Rust 的探测在独立进程里，一条控制台日志都不产生；前端每失败一次就糊一行
    // ERR_CONNECTION_REFUSED，且那是 WebView 自动打印、JS 无法抑制的。
    if ((window as any).__TAURI_INTERNALS__) {
      setBackendReady(false)  // 纵深防御：初值已按环境判定，这里再复位一次
      let done = false
      const initOnce = () => { if (!done) { done = true; setBackendReady(true); init() } }
      continueStartupRef.current = initOnce

      let unlistenReady = () => {}
      let unlistenTimeout = () => {}
      let unlistenFailed = () => {}
      let unlistenWarning = () => {}
      import('@tauri-apps/api/event').then(({ listen }) => {
        // ① 主路径：后端就绪即唤醒（冷启动 2s 左右），此前一条请求都不用发
        listen('backend-ready', () => initOnce()).then(un => { unlistenReady = un })
        // ② Rust 探测 45s 超时 —— 进程**还活着但没就绪**。**停在诊断屏，不自动放行**：
        //    放行的结果是满屏 ERR_CONNECTION_REFUSED + 一句 console.warn，
        //    用户既看不到原因、也不知道日志在哪（实测反馈正是「还是没有任何日志」）。
        //    失败屏上有一句话结论、两段日志、退出码与日志路径，「仍然继续」按钮
        //    保留改造前的放行能力 —— 知情之后再选，而不是被推着往前走。
        listen<StartupFailure>('backend-startup-timeout', e => {
          setStartupFailure(e.payload)
          console.warn('后端启动超时（进程仍在运行）—— 停在诊断屏')
        }).then(un => { unlistenTimeout = un })
        // ③ 确定失败（进程根本没起来 / 起来就退）—— **不放行**，停在失败屏上。
        //    放行只会换来满屏 ERR_CONNECTION_REFUSED，而原因就在那一屏里。
        listen<StartupFailure>('backend-startup-failed', e => {
          setStartupFailure(e.payload)
          console.error('后端启动失败:', e.payload.summary)
        }).then(un => { unlistenFailed = un })
        // ④ 安装路径告警 —— Rust 一直在发，此前前端零监听（等于白发）。故意
        //    **不挂 init**：它描述的正是「后端可能起不来」的原因，等 init 就太晚了。
        listen<InstallPathWarning>('backend-warning', e => {
          const text = installWarningText(e.payload)
          // 用户主动关过就一直不再显示（跨会话，见 `installWarning.ts`）——
          // Rust 每次启动都会重新发这条事件，只清 state 的话每次开机都要再关一遍。
          if (text && !isInstallWarningDismissed()) setInstallWarning(text)
        }).then(un => { unlistenWarning = un })
      }).catch(() => {}) // 事件模块导入失败也不要紧：静默期探测 + 硬兜底仍能放行

      // ③ 静默期过后探测一次 —— 覆盖「页面刷新时后端已在运行、backend-ready 早已错过」。
      //    刻意不做循环轮询：那正是控制台噪音的来源，而正常冷启动已被事件覆盖。
      const probeTimer = setTimeout(async () => {
        if (done) return
        try {
          const r = await fetch('http://127.0.0.1:7413/health')
          if (r.ok) initOnce()
        } catch { /* 仍未就绪 —— 继续等 backend-ready 事件 */ }
      }, BACKEND_PROBE_DELAY_MS)

      // ④ 硬兜底：两个事件都没到达时也不能永久停在加载屏。
      //    多半是「进程在 WebView 加载完成前就死了、emit 被丢掉」—— 主动拉一次
      //    managed state 里的诊断；拉到**确定失败**就停在失败屏，而不是放行。
      const hardTimer = setTimeout(async () => {
        if (done) return
        try {
          const { invoke } = await import('@tauri-apps/api/core')
          const f = await invoke<StartupFailure | null>('get_backend_startup_failure')
          if (f) {
            // 有任何诊断都停屏 —— `alive` 只决定文案，不再决定「要不要让用户看见」。
            setStartupFailure(f)
            console.error('后端启动失败:', f.summary)
            return
          }
        } catch { /* 拿不到诊断就退回改造前的行为 */ }
        console.warn('后端就绪信号超时，强制加载')
        initOnce()
      }, BACKEND_HARD_TIMEOUT_MS)

      return () => {
        done = true
        clearTimeout(probeTimer)
        clearTimeout(hardTimer)
        continueStartupRef.current = null
        unlistenReady()
        unlistenTimeout()
        unlistenFailed()
        unlistenWarning()
        unsubConfig()
      }
    }
    // 浏览器/开发模式：直接加载（backendReady 默认 true）
    init()
    return unsubConfig
  }, [refreshCapabilities])

  // Tauri 冷启动：后端未就绪时显示加载屏，避免组件提前发请求导致 CONNECTION_REFUSED。
  // ⚠️ **有任何诊断就换失败屏**（不再只判 `alive`）——那一屏上有退出码与两段日志，
  //    是用户唯一够得到的排查入口（详见 `lib/startupFailure.ts` 的模块注释）。
  //    `alive` 只影响失败屏上那句「进程仍在运行」的提示（慢 vs 死的下一步动作不同）。
  if (!backendReady) {
    if (startupFailure) {
      return (
        <BackendStartupFailure
          failure={startupFailure}
          onRetryStarted={() => setStartupFailure(null)}
          onFailureUpdate={setStartupFailure}
          onContinue={() => continueStartupRef.current?.()}
        />
      )
    }
    return (
      <div className="h-screen flex flex-col items-center justify-center gap-4 bg-surface">
        <img src="/logo.png" className="w-16 h-16 rounded-xl object-cover animate-pulse" alt="LiMo" />
        <div className="text-[15px] font-medium text-dimmed">正在启动后端服务…</div>
        <div className="text-[13px] text-muted">首次启动可能需要 10-30 秒</div>
      </div>
    )
  }

  return (
    <div className="h-screen flex flex-col overflow-hidden" onKeyDown={handleAppKeyDown} tabIndex={-1}>
      {/* 首次启动向导 */}
      {showSetup && (
        <OnboardingWizard onDone={() => {
          try { localStorage.setItem('limo_setup_done', '1') } catch {}
          setShowSetup(false)
        }} />
      )}
      {/* 顶部栏（沉浸下不渲染 —— 它不在插槽系统里，`hideSlots` 管不到，必须显式控制） */}
      {!immersive && <header className="glass border-b border-border-light h-12 flex items-center px-4 shrink-0 z-10">
        <div className="flex items-center gap-2.5">
          <img src="/logo.png" className="w-7 h-7 rounded-lg object-cover" alt="LiMo" />
          <span className="font-semibold text-[15px]">LiMo</span>
        </div>
        {/* 顶栏只留「全局工具」。面板入口（生成 / 文档库 / AI / 设置）已全部收进左侧
            活动栏 —— 刻意**不在顶栏保留第二套按钮**：两处各写一遍必然漂移（一处忘了
            互斥、一处忘了宽度复位），而今天这份漂移是用户能直接看见的（两个面板同开）。 */}
        <div className="flex items-center gap-0.5 ml-6">
          <TaskPanelToggle />
          <div className="w-px h-5 bg-border-default" />
          <ThemeToggle />
          <button onClick={() => setShowExport(true)} className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg hover:bg-surface-hover text-muted hover:text-dimmed transition-colors" title="批量导出 · 侧边面板"><Archive size={15} /><span className="text-[12px]">导出</span></button>
        </div>
        <SearchBar />
        <div className="flex-1" />
      </header>}

      {/* 后端断开横幅（P1.1 持续探活）— 运行中断开提示，恢复自动消失 */}
      {backendStatus !== 'ready' && (
        <div className={`flex items-center gap-2 px-4 py-1.5 text-[12px] font-medium border-b border-border-light shrink-0 z-10 ${backendStatus === 'crashed' ? 'bg-error/10 text-error' : 'bg-warning/10 text-warning'}`}>
          <span className={`w-2 h-2 rounded-full animate-pulse ${backendStatus === 'crashed' ? 'bg-error' : 'bg-warning'}`} />
          {backendStatus === 'crashed'
            ? '后端已崩溃，请重启应用'
            : '后端服务异常，已断开，正在重试…'}
        </div>
      )}

      {/* 安装路径告警（Rust backend-warning）— 可关闭，且**关过就不再出现**：
          它是**建议**而非故障（诊断每次启动都跑，用户没关就一直提示；关了则本机
          永久静默，理由见 `lib/installWarning.ts`）。 */}
      {installWarning && (
        <div className="flex items-center gap-2 px-4 py-1.5 text-[12px] font-medium border-b border-border-light shrink-0 z-10 bg-warning/10 text-warning">
          <span className="w-2 h-2 rounded-full bg-warning shrink-0" />
          <span className="flex-1">{installWarning}</span>
          <button
            onClick={() => { dismissInstallWarning(); setInstallWarning('') }}
            className="rounded p-0.5 hover:bg-surface-hover" title="关闭（不再提示）"><X size={12} /></button>
        </div>
      )}

      {/* 主体 — 活动栏 + 插槽布局引擎 */}
      <div className="flex flex-1 overflow-hidden">
        {/* 活动栏常驻最左（沉浸下让位，与顶栏一致 —— 沉浸要藏掉所有面板入口）。
            它**不是** slot：图标来自 `SlotRegistry.grouped().left` 的派生，不加新机制。 */}
        {!immersive && <ActivityBar onOpenSettings={() => setShowSettings(true)} />}
        {/* hideSlots 是**真卸载**（SlotLayout 直接 return null），不是 CSS 隐藏 —— 故进入
            沉浸前要过 processingTasks 守卫（见 uiSlice.toggleImmersive），否则正在跑的
            任务会因 GenPanel 卸载变僵尸。预览区（center）不在隐藏名单里。 */}
        <SlotLayout hideSlots={immersive ? ['genPanel', 'docLib'] : undefined} />
      </div>

      {/* AI 浮窗（唯一形态，2026-08-04 移除插槽面板） — 沉浸下让位（位置/尺寸本就持久化，退出即恢复） */}
      {aiVisible && !immersive && <FloatingAI />}

      {/* Toast */}
      <Toast />

      {/* TaskPanel */}
      <TaskPanel />

      {/* ExportPanel — 同样是「操作」入口，沉浸下让位 */}
      {showExport && !immersive && <ExportPanel onClose={() => setShowExport(false)} />}

      {/* 设置 — overlay，不卸载主界面 */}
      {showSettings && (
        <div className="fixed inset-0 z-[10000] flex flex-col bg-surface">
          <SettingsPage onBack={() => setShowSettings(false)} />
        </div>
      )}
    </div>
  )
}
