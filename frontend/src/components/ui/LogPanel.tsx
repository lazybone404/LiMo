import { useState, useEffect, useRef } from 'react'
import { X, Copy, RefreshCw, Terminal } from 'lucide-react'
import { apiFetch, apiUrl } from '../../api/client'
import { useStore } from '../../store'

interface LogEntry {
  ts: number
  level: string
  name: string
  msg: string
}

const LEVEL_COLORS: Record<string, string> = {
  error: 'text-error',
  warning: 'text-warning',
  info: 'text-success',
  debug: 'text-dimmed',
}

export function LogPanel({ onClose }: { onClose: () => void }) {
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [levelFilter, setLevelFilter] = useState('all')
  const addToast = useStore(s => s.addToast)
  const scrollRef = useRef<HTMLDivElement>(null)
  const intervalRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined)

  const fetchLogs = async () => {
    try {
      const r = await apiFetch(apiUrl(`/logs?lines=300&level=${levelFilter}`))
      const data = await r.json()
      setLogs(data.logs || [])
    } catch {
      // 连续失败时显示提示（避免连接数限制或网络波动导致空白）
      if (logs.length === 0) setErrorCount(c => c + 1)
    }
    finally { setLoading(false) }
  }

  const [errorCount, setErrorCount] = useState(0)

  // 首次加载 + 每 3 秒刷新
  useEffect(() => {
    setLoading(true)
    setErrorCount(0)
    fetchLogs()
    intervalRef.current = setInterval(fetchLogs, 3000)
    return () => clearInterval(intervalRef.current)
  }, [levelFilter])

  // 新日志自动滚底
  useEffect(() => {
    if (scrollRef.current)
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
  }, [logs])

  const handleCopy = async () => {
    const text = logs.map(l => l.msg).join('\n')
    try {
      await navigator.clipboard.writeText(text)
      addToast('日志已复制到剪贴板')
    } catch {
      addToast('复制失败，请手动复制', 'error')
    }
  }

  return (
    <>
      {/* 遮罩 */}
      <div className="fixed inset-0 z-[9998] bg-black/20" onClick={onClose} />

      <aside className="fixed right-0 bottom-0 top-0 z-[9999] flex w-[520px] max-w-[92vw] flex-col border-l bg-elevated shadow-2xl">
        {/* 头部 */}
        <header className="flex items-center justify-between border-b px-4 py-3 shrink-0">
          <div className="flex items-center gap-2">
            <Terminal size={16} className="text-dimmed" />
            <h2 className="text-[15px] font-semibold">系统日志</h2>
          </div>
          <div className="flex items-center gap-1">
            <select value={levelFilter} onChange={e => setLevelFilter(e.target.value)}
              className="px-2 py-1 bg-surface border border-border-default rounded-md text-[11px] outline-none">
              <option value="all">全部</option>
              <option value="error">ERROR</option>
              <option value="warning">WARNING</option>
              <option value="info">INFO</option>
              <option value="debug">DEBUG</option>
            </select>
            <button onClick={fetchLogs} disabled={loading}
              className="p-1.5 rounded-md hover:bg-surface text-dimmed disabled:opacity-30"
              title="刷新"><RefreshCw size={14} className={loading ? 'animate-spin' : ''} /></button>
            <button onClick={handleCopy}
              className="p-1.5 rounded-md hover:bg-surface text-dimmed"
              title="复制日志"><Copy size={14} /></button>
            <button onClick={onClose}
              className="p-1.5 rounded-md hover:bg-surface text-dimmed" title="关闭"><X size={14} /></button>
          </div>
        </header>

        {/* 日志列表 */}
        <div ref={scrollRef} className="flex-1 overflow-auto bg-[#1E1E1E] p-3 font-mono text-xs leading-relaxed">
          {logs.length === 0 ? (
            <div className="text-dimmed italic pt-4 text-center">
              {errorCount > 3 ? '日志加载失败，请检查后端是否运行' : '暂无日志'}
            </div>
          ) : (
            logs.map((l, i) => (
              <div key={`${l.ts}-${i}`} className="whitespace-pre-wrap break-all flex gap-2 hover:bg-elevated/5 px-1 rounded">
                <span className="text-dimmed shrink-0 select-none">
                  {new Date(l.ts * 1000).toISOString().slice(11, 23)}
                </span>
                <span className={`${LEVEL_COLORS[l.level] || 'text-disabled'} shrink-0 w-10 select-none`}>
                  {l.level.toUpperCase()}
                </span>
                <span className="text-disabled break-all">{l.msg}</span>
              </div>
            ))
          )}
        </div>

        {/* 底部 */}
        <footer className="border-t px-4 py-2 text-xs text-disabled shrink-0 flex items-center justify-between">
          <span>最近 {logs.length} 行 · 每 3 秒自动刷新</span>
          <span>日志存于内存，重启后清空</span>
        </footer>
      </aside>
    </>
  )
}
