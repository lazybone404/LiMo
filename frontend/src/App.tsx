import { useEffect, useState, useCallback } from 'react'
import { useStore } from './store'
import { GenPanel } from './components/gen-panel/GenPanel'
import { Toast } from './components/ui/Toast'
import { TaskPanel, TaskPanelToggle } from './components/ui/TaskPanel'
import { ExportPanel } from './components/ui/ExportPanel'
import { DocLibrary } from './components/doc-library/DocLibrary'
import { PreviewArea } from './components/preview/PreviewArea'
import { AIPanel } from './components/ai-chat/AIPanel'
import { FloatingAI } from './components/ai-chat/FloatingAI'
import { SettingsPage } from './components/settings/SettingsPage'
import { OnboardingWizard } from './components/ui/OnboardingWizard'
import { ResizeHandle } from './components/ui/ResizeHandle'
import { PanelStrip } from './components/ui/PanelStrip'
import { ThemeToggle } from './components/ui/ThemeToggle'
import { fetchCapabilities, listTranscripts, getMarkdown, getTranscript } from './api/client'
import { Settings, PanelLeft, Library, MessageSquare, PenLine, Bot, Archive } from 'lucide-react'

export default function App() {
  const genPanelOpen = useStore(s => s.genPanelOpen)
  const aiVisible = useStore(s => s.aiVisible)
  const aiFloating = useStore(s => s.aiFloating)
  const setCapabilities = useStore(s => s.setCapabilities)
  const setDocs = useStore(s => s.setDocs)
  const [showSettings, setShowSettings] = useState(false)
  const [showExport, setShowExport] = useState(false)
  const [showSetup, setShowSetup] = useState(() => {
    // 首次启动检测（localStorage 标记）
    try { return !localStorage.getItem('limo_setup_done') } catch { return false }
  })

  const panelWidths = useStore(s => s.panelWidths)
  const panelsCollapsed = useStore(s => s.panelsCollapsed)
  const setPanelWidth = useStore(s => s.setPanelWidth)
  const setPanelCollapsed = useStore(s => s.setPanelCollapsed)
  const toggleGenPanel = useStore(s => s.toggleGenPanel)
  const toggleAI = useStore(s => s.toggleAI)
  const setAiFloating = useStore(s => s.setAiFloating)
  const backendReady = useStore(s => s.backendReady)
  const setBackendReady = useStore(s => s.setBackendReady)

  useEffect(() => {
    const init = () => {
      fetchCapabilities().then(setCapabilities).catch(err => {
        console.error('加载能力配置失败:', err)
        useStore.getState().addToast('加载能力配置失败，请检查后端是否运行', 'error')
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
            if (r.ok) { console.log('Backend health OK'); initOnce(); return }
          } catch { /* 后端未就绪，继续轮询 */ }
          await new Promise(r => setTimeout(r, 500))
        }
        if (!done) { console.warn('Backend ready timeout, forcing load'); initOnce() }
      }
      healthCheck()

      // 同时监听 backend-ready 事件（后端重启等场景）
      import('@tauri-apps/api/event').then(({ listen }) => {
        listen('backend-ready', () => { console.log('Backend ready event'); initOnce() })
      }).catch(() => {}) // 导入失败无所谓，health check 已覆盖

      return () => { done = true }
    }
    // 浏览器/开发模式：直接加载（backendReady 默认 true）
    init()
  }, [])

  // 面板拖拽回调 — 用 getState() 读最新宽度，避免 stale closure
  const resizeGen = useCallback((dx: number) => {
    const w = useStore.getState().panelWidths.genPanel
    setPanelWidth('genPanel', Math.max(50, Math.min(600, w + dx)))
  }, [setPanelWidth])
  const resizeDocLib = useCallback((dx: number) => {
    const w = useStore.getState().panelWidths.docLib
    setPanelWidth('docLib', Math.max(50, Math.min(600, w + dx)))
  }, [setPanelWidth])
  const resizeAI = useCallback((dx: number) => {
    const w = useStore.getState().panelWidths.aiPanel
    setPanelWidth('aiPanel', Math.max(50, Math.min(600, w + (-dx))))
  }, [setPanelWidth])

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
            if (useStore.getState().generating || Object.keys(useStore.getState().processingTasks).length > 0) {
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
            className={`px-2.5 py-1 rounded-md text-[12px] transition-all flex items-center gap-1.5 ${genPanelOpen ? 'bg-elevated font-medium shadow-sm text-foreground' : 'text-muted hover:text-dimmed'}`}
            title={genPanelOpen ? '收起生成面板' : '展开生成面板'}
          >
            <PanelLeft size={13} />
            <span>生成</span>
          </button>
          <button
            onClick={() => {
              if (panelsCollapsed.docLib) {
                setPanelCollapsed('docLib', false)
                setPanelWidth('docLib', 260)
              } else {
                setPanelCollapsed('docLib', true)
              }
            }}
            className={`px-2.5 py-1 rounded-md text-[12px] transition-all flex items-center gap-1.5 ${!panelsCollapsed.docLib ? 'bg-elevated font-medium shadow-sm text-foreground' : 'text-muted hover:text-dimmed'}`}
            title={!panelsCollapsed.docLib ? '收起文档库' : '展开文档库'}
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
            className={`px-2.5 py-1 rounded-md text-[12px] transition-all flex items-center gap-1.5 ${aiVisible && !aiFloating ? 'bg-elevated font-medium shadow-sm text-foreground' : 'text-muted hover:text-dimmed'}`}
            title={aiVisible && !aiFloating ? '收起 AI 面板' : '展开 AI 面板'}
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
        <div className="flex-1" />
      </header>

      {/* 主体 — 可拖拽四栏 */}
      <div className="flex flex-1 overflow-hidden">
        {/* 生成面板 */}
        {genPanelOpen && !panelsCollapsed.genPanel ? (
          <div style={{ width: panelWidths.genPanel, minWidth: 80, maxWidth: 600 }} className="shrink-0">
            <GenPanel />
          </div>
        ) : genPanelOpen ? (
          <PanelStrip label="生成" side="left" icon={PenLine} onClick={() => {
            setPanelCollapsed('genPanel', false)
            setPanelWidth('genPanel', 260)
          }} />
        ) : null}
        {genPanelOpen && !panelsCollapsed.genPanel && (
          <ResizeHandle onResize={resizeGen} />
        )}

        {/* 文档库 */}
        {!panelsCollapsed.docLib ? (
          <div style={{ width: panelWidths.docLib, minWidth: 80, maxWidth: 600 }} className="shrink-0">
            <DocLibrary />
          </div>
        ) : (
          <PanelStrip label="文档库" side="left" icon={Library} onClick={() => {
            setPanelCollapsed('docLib', false)
            setPanelWidth('docLib', 260)
          }} />
        )}
        {!panelsCollapsed.docLib && (
          <ResizeHandle onResize={resizeDocLib} />
        )}

        {/* 预览区 */}
        <div className="flex-1 flex overflow-hidden min-w-[200px]">
          <PreviewArea />
        </div>

        {/* AI 面板 — 停靠模式 */}
        {aiVisible && !aiFloating && (
          <>
            <ResizeHandle onResize={resizeAI} />
            {!panelsCollapsed.aiPanel ? (
              <div style={{ width: panelWidths.aiPanel, minWidth: 80, maxWidth: 600 }} className="shrink-0">
                <AIPanel />
              </div>
            ) : (
              <PanelStrip label="AI" side="right" icon={Bot} onClick={() => {
                setPanelCollapsed('aiPanel', false)
                setPanelWidth('aiPanel', 340)
              }} />
            )}
          </>
        )}
      </div>

      {/* AI 浮窗 */}
      {aiVisible && aiFloating && <FloatingAI />}

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
