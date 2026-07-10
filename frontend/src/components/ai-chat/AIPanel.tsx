import { useState } from 'react'
import { useStore } from '../../store'
import { ChatCore } from './ChatCore'
import { PanelLeftClose, ExternalLink } from 'lucide-react'

export function AIPanel() {
  const width = useStore(s => s.panelWidths.aiPanel)
  const setAiFloating = useStore(s => s.setAiFloating)
  const setConversations = useStore(s => s.setConversations)
  const setCurrentConversationId = useStore(s => s.setCurrentConversationId)

  const [listCollapsed, setListCollapsed] = useState(false)

  const listWidth = listCollapsed ? 0 : Math.max(100, width * 0.35)
  const showNav = !listCollapsed && width > 480
  const navWidth = showNav ? Math.max(100, width * 0.2) : 0
  const compact = listWidth < 100

  return (
    <aside className="w-full h-full border-l border-border-light flex flex-col bg-elevated/60 backdrop-blur-[20px]" onContextMenu={e => e.preventDefault()}>
      <ChatCore
        compact={compact}
        listWidth={listWidth}
        navWidth={navWidth}
        showNav={showNav}
        listCollapsed={listCollapsed}
        onListCollapsedChange={setListCollapsed}
        titleRight={
          <>
            <button onClick={() => useStore.getState().setPanelCollapsed('aiPanel', true)} className="w-6 h-6 rounded-md hover:bg-surface-hover flex items-center justify-center" title="收起面板"><PanelLeftClose size={14} className="text-muted" /></button>
            <button onClick={() => setAiFloating(true)} className="w-6 h-6 rounded-md hover:bg-surface-hover flex items-center justify-center" title="弹出浮窗"><ExternalLink size={14} className="text-muted" /></button>
          </>
        }
        onConversationsChange={setConversations}
        onCurrentConvChange={setCurrentConversationId}
      />
    </aside>
  )
}
