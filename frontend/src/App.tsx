import { useEffect, useState, useCallback } from 'react'
import { useStore } from './store'
import { onConfigChanged } from './lib/configEvents'
import { Toast } from './components/ui/Toast'
import { TaskPanel, TaskPanelToggle } from './components/ui/TaskPanel'
import { ExportPanel } from './components/ui/ExportPanel'
import { FloatingAI } from './components/ai-chat/FloatingAI'
import { SettingsPage } from './components/settings/SettingsPage'
import { OnboardingWizard } from './components/ui/OnboardingWizard'
import { SlotLayout } from './components/ui/SlotLayout'
import { SearchBar } from './components/ui/SearchBar'
import { ThemeToggle } from './components/ui/ThemeToggle'
import { fetchCapabilities, listTranscripts, getMarkdown, getTranscript } from './api/client'
import { Settings, PanelLeft, Library, MessageSquare, Archive } from 'lucide-react'

export default function App() {
  const slots = useStore(s => s.slots)
  const setSlot = useStore(s => s.setSlot)
  const aiFloating = useStore(s => s.aiFloating)
  const setCapabilities = useStore(s => s.setCapabilities)
  const setDocs = useStore(s => s.setDocs)
  const [showSettings, setShowSettings] = useState(false)
  const [showExport, setShowExport] = useState(false)
  const [showSetup, setShowSetup] = useState(() => {
    try { return !localStorage.getItem('limo_setup_done') } catch { return false }
  })
  const toggleGenPanel = useStore(s => s.toggleGenPanel)
  const toggleAI = useStore(s => s.toggleAI)
  const setAiFloating = useStore(s => s.setAiFloating)
  const backendReady = useStore(s => s.backendReady)
  const setBackendReady = useStore(s => s.setBackendReady)

  // 刷新 capabilities（引擎下拉/场景标签/文件类型的数据源）。
  // checkInterrupted：仅初始化时检查未完成生成标记，配置刷新不重复提示
  const refreshCapabilities = useCallback((checkInterrupted: boolean) => {
    fetchCapabilities().then(caps => {
      setCapabilities(caps)
      if (checkInterrupted) {
        // 未完成生成标记（应用重启/崩溃残留）→ 提示
        const interrupted = (caps as any).interruptedGenerations
        if (Array.isArray(interrupted) && interrupted.length > 0) {
          useStore.getState().addToast(`检测到 ${interrupted.length} 个未完成的生成（上次应用中断），请重新生成`, 'info')
        }
      }
    }).catch(err => {
      console.error('加载能力配置失败:', err)
      useStore.getState().addToast('加载能力配置失败，请检查后端是否运行', 'error')
    })
  }, [setCapabilities])

  useEffect(() => {
    // 订阅设置页配置变更 → 刷新 capabilities（保存后无需手动刷新页面）
    const unsubConfig = onConfigChanged(() => refreshCapabilities(false))

    const init = () => {
      refreshCapabilities(true)
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
              useStore.getState().setMarkdownMeta({ id: md.id, style: md.style, mode: md.mode, version: md.version })
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
    })
  }

    // Tauri 模式：主动轮询 /health + 监听 backend-ready 事件，双保险
    if ((window as any).__TAURI_INTERNALS__) {
      setBackendReady(false)  // 阻止 UI 提前渲染，避免 CONNECTION_REFUSED
      let done = false
      const initOnce = () => { if (!done) { done = true; setBackendReady(true); init() } }

      // 主动健康检查：后端刷新前可能已在运行，event 已错过
      const healthCheck = async () => {
        for (let i = 0; i < 90; i++) {  // 90 × 500ms = 45s
          if (done) return
          try {
            const r = await fetch('http://127.0.0.1:7413/health')
            if (r.ok) { initOnce(); return }
          } catch { /* 后端未就绪，继续轮询 */ }
          await new Promise(r => setTimeout(r, 500))
        }
        if (!done) { console.warn('Backend ready timeout, forcing load'); initOnce() }
      }
      healthCheck()

      // 同时监听 backend-ready 事件（后端重启等场景）
      import('@tauri-apps/api/event').then(({ listen }) => {
        listen('backend-ready', () => { initOnce() })
      }).catch(() => {}) // 导入失败无所谓，health check 已覆盖

      return () => { done = true; unsubConfig() }
    }
    // 浏览器/开发模式：直接加载（backendReady 默认 true）
    init()
    return unsubConfig
  }, [])

  // Tauri 冷启动：后端未就绪时显示加载屏，避免组件提前发请求导致 CONNECTION_REFUSED
  if (!backendReady) {
    return (
      <div className="h-screen flex flex-col items-center justify-center gap-4 bg-surface">
        <img src="/logo.png" className="w-16 h-16 rounded-xl object-cover animate-pulse" alt="LiMo" />
        <div className="text-[15px] font-medium text-dimmed">正在启动后端服务…</div>
        <div className="text-[13px] text-muted">首次启动可能需要 10-30 秒</div>
      </div>
    )
  }

  return (
    <div className="h-screen flex flex-col overflow-hidden" onKeyDown={(e) => {
      if (e.metaKey || e.ctrlKey) {
        switch (e.key) {
          case 'g':
            e.preventDefault()
            // 操作进行中时拦截，防止面板卸载导致任务变僵尸
            if (Object.keys(useStore.getState().processingTasks).length > 0) {
              useStore.getState().addToast('操作进行中，请完成或取消后再切换面板', 'info')
            } else {
              toggleGenPanel()
            }
            break
          case 'i': e.preventDefault(); useStore.getState().toggleAI(); break
          case 'w': e.preventDefault(); useStore.getState().cycleContentWidth(); break
        }
      }
    }} tabIndex={-1}>
      {/* 首次启动向导 */}
      {showSetup && (
        <OnboardingWizard onDone={() => {
          try { localStorage.setItem('limo_setup_done', '1') } catch {}
          setShowSetup(false)
        }} />
      )}
      {/* 顶部栏 */}
      <header className="glass border-b border-border-light h-12 flex items-center px-4 shrink-0 z-10">
        <div className="flex items-center gap-2.5">
          <img src="/logo.png" className="w-7 h-7 rounded-lg object-cover" alt="LiMo" />
          <span className="font-semibold text-[15px]">LiMo</span>
        </div>
        {/* 集中式面板切换 — 对标 BiliNote 工具栏 */}
        <div className="flex items-center gap-0.5 ml-6 bg-surface rounded-lg p-0.5">
          <button
            onClick={toggleGenPanel}
            className={`px-2.5 py-1 rounded-md text-[12px] transition-all flex items-center gap-1.5 ${slots.genPanel.visible ? 'bg-elevated font-medium shadow-sm text-foreground' : 'text-muted hover:text-dimmed'}`}
            title={slots.genPanel.visible ? '收起生成面板' : '展开生成面板'}
          >
            <PanelLeft size={13} />
            <span>生成</span>
          </button>
          <button
            onClick={() => {
              if (slots.docLib.collapsed) {
                setSlot('docLib', { collapsed: false, width: 260 })
              } else {
                setSlot('docLib', { collapsed: true })
              }
            }}
            className={`px-2.5 py-1 rounded-md text-[12px] transition-all flex items-center gap-1.5 ${!slots.docLib.collapsed ? 'bg-elevated font-medium shadow-sm text-foreground' : 'text-muted hover:text-dimmed'}`}
            title={!slots.docLib.collapsed ? '收起文档库' : '展开文档库'}
          >
            <Library size={13} />
            <span>文档库</span>
          </button>
          <button
            onClick={() => {
              if (aiFloating) {
                setAiFloating(false)
              } else {
                toggleAI()
              }
            }}
            className={`px-2.5 py-1 rounded-md text-[12px] transition-all flex items-center gap-1.5 ${slots.aiPanel.visible && !aiFloating ? 'bg-elevated font-medium shadow-sm text-foreground' : 'text-muted hover:text-dimmed'}`}
            title={slots.aiPanel.visible && !aiFloating ? '收起 AI 面板' : '展开 AI 面板'}
          >
            <MessageSquare size={13} />
            <span>AI</span>
          </button>
          <div className="w-px h-5 bg-border-default" />
          <button
            onClick={() => setShowSettings(true)}
            className={`px-2.5 py-1 rounded-md text-[12px] transition-all flex items-center gap-1.5 text-muted hover:text-dimmed`}
            title="设置"
          >
            <Settings size={13} />
          </button>
          <TaskPanelToggle />
          <div className="w-px h-5 bg-border-default" />
          <ThemeToggle />
          <button onClick={() => setShowExport(true)} className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg hover:bg-surface-hover text-muted hover:text-dimmed transition-colors" title="批量导出 · 侧边面板"><Archive size={15} /><span className="text-[12px]">导出</span></button>
        </div>
        <SearchBar />
        <div className="flex-1" />
      </header>

      {/* 主体 — 插槽布局引擎 */}
      <div className="flex flex-1 overflow-hidden">
        <SlotLayout hideSlots={aiFloating ? ['aiPanel'] : undefined} />
      </div>

      {/* AI 浮窗 */}
      {slots.aiPanel.visible && aiFloating && <FloatingAI />}

      {/* Toast */}
      <Toast />

      {/* TaskPanel */}
      <TaskPanel />

      {/* ExportPanel */}
      {showExport && <ExportPanel onClose={() => setShowExport(false)} />}

      {/* 设置 — overlay，不卸载主界面 */}
      {showSettings && (
        <div className="fixed inset-0 z-[10000] flex flex-col bg-surface">
          <SettingsPage onBack={() => setShowSettings(false)} />
        </div>
      )}
    </div>
  )
}
