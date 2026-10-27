import { useState, useEffect, useRef } from 'react'
import { X, Copy, RefreshCw, Terminal, Gauge } from 'lucide-react'
import { apiFetch, apiUrl } from '../../api/client'
import { useStore } from '../../store'

interface LogEntry {
  ts: number
  level: string
  name: string
  msg: string
}

/** `GET /api/perf` 的形状（后端 `app/core/perf.py::snapshot()`）。 */
interface PerfGroup {
  name: string
  count: number
  min: number
  p50: number
  p95: number
  max: number
  avg: number
}

interface PerfSnapshot {
  groups: PerfGroup[]
  totalSamples: number
  /** 落盘路径；`null` = 落盘不可用（目录不可写），只剩进程内统计 */
  logFile: string | null
  keepDays: number
  startedAt?: string | null
}

const LEVEL_COLORS: Record<string, string> = {
  error: 'text-error',
  warning: 'text-warning',
  info: 'text-success',
  debug: 'text-dimmed',
}

/** 毫秒数转人看的单位（>=1s 换成秒，保留一位小数）。 */
function fmtMs(v: number): string {
  return v < 1000 ? `${v}ms` : `${(v / 1000).toFixed(1)}s`
}

/** 性能表复制成 Markdown —— 用户贴给 agent 就能直接定位到哪一类慢。 */
function perfToText(perf: PerfSnapshot | null): string {
  if (!perf || perf.groups.length === 0) return '(暂无性能打点数据)'
  const head = `LiMo 性能打点 · ${perf.totalSamples} 条样本` +
    (perf.startedAt ? ` · 进程启动于 ${perf.startedAt}` : '') +
    (perf.logFile ? `\n落盘: ${perf.logFile}（保留 ${perf.keepDays} 天）` : '\n落盘: 不可用（仅进程内统计）')
  const rows = perf.groups.map(g =>
    `| ${g.name} | ${g.count} | ${g.p50} | ${g.p95} | ${g.max} |`)
  return [
    head, '',
    '| 打点 | 次数 | p50 | p95 | max |',
    '| --- | ---: | ---: | ---: | ---: |',
    ...rows,
    '',
    '> 带 [ttfb] 的是流式端点，量到的是建连时间而非流总时长。',
  ].join('\n')
}

export function LogPanel({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<'logs' | 'perf'>('logs')
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [perf, setPerf] = useState<PerfSnapshot | null>(null)
  const [perfError, setPerfError] = useState(false)
  const [loading, setLoading] = useState(false)
  const [levelFilter, setLevelFilter] = useState('all')
  const [search, setSearch] = useState('')
  const addToast = useStore(s => s.addToast)
  const scrollRef = useRef<HTMLDivElement>(null)
  const intervalRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined)
  const logsEmptyRef = useRef(true)  // 用 ref 避免 setInterval 闭包陈旧问题

  const [errorCount, setErrorCount] = useState(0)

  const fetchLogs = async () => {
    try {
      const q = `/logs?lines=300&level=${levelFilter}` +
        (search.trim() ? `&search=${encodeURIComponent(search.trim())}` : '')
      const r = await apiFetch(apiUrl(q))
      const data = await r.json()
      const entries = data.logs || []
      setLogs(entries)
      logsEmptyRef.current = entries.length === 0
    } catch {
      // 连续失败时显示提示（避免连接数限制或网络波动导致空白）
      if (logsEmptyRef.current) setErrorCount(c => c + 1)
    }
    finally { setLoading(false) }
  }

  const fetchPerf = async () => {
    try {
      const r = await apiFetch(apiUrl('/perf'))
      setPerf(await r.json())
      setPerfError(false)
    } catch {
      setPerfError(true)
    } finally { setLoading(false) }
  }

  // 首次加载 + 每 3 秒刷新当前 tab（切 tab / level / search 变化时立即刷新）
  useEffect(() => {
    setLoading(true)
    setErrorCount(0)
    const tick = tab === 'logs' ? fetchLogs : fetchPerf
    tick()
    intervalRef.current = setInterval(tick, 3000)
    return () => clearInterval(intervalRef.current)
  }, [tab, levelFilter, search])

  // 新日志自动滚底（只对日志 tab；性能表是按 p95 排的，滚底没有意义）
  useEffect(() => {
    if (tab === 'logs' && scrollRef.current)
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
  }, [logs, tab])

  const handleCopy = async () => {
    const text = tab === 'logs' ? logs.map(l => l.msg).join('\n') : perfToText(perf)
    try {
      await navigator.clipboard.writeText(text)
      addToast(tab === 'logs' ? '日志已复制到剪贴板' : '性能数据已复制为 Markdown')
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
            {tab === 'logs' ? <Terminal size={16} className="text-dimmed" /> : <Gauge size={16} className="text-dimmed" />}
            <div className="flex items-center gap-1" role="tablist">
              {([['logs', '系统日志'], ['perf', '性能']] as const).map(([k, label]) => (
                <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
                  className={`px-2 py-1 rounded-md text-[13px] transition-colors ${
                    tab === k ? 'font-semibold text-accent bg-accent/10' : 'text-dimmed hover:bg-surface'
                  }`}>{label}</button>
              ))}
            </div>
          </div>
          <div className="flex items-center gap-1">
            {tab === 'logs' && (
              <>
                <input
                  value={search}
                  onChange={e => setSearch(e.target.value)}
                  placeholder="搜索关键字 / request_id"
                  className="w-36 px-2 py-1 bg-surface border border-border-default rounded-md text-[11px] outline-none focus:border-accent placeholder:text-disabled"
                />
                <select value={levelFilter} onChange={e => setLevelFilter(e.target.value)}
                  className="px-2 py-1 bg-surface border border-border-default rounded-md text-[11px] outline-none">
                  <option value="all">全部</option>
                  <option value="error">ERROR</option>
                  <option value="warning">WARNING</option>
                  <option value="info">INFO</option>
                  <option value="debug">DEBUG</option>
                </select>
              </>
            )}
            <button onClick={() => (tab === 'logs' ? fetchLogs() : fetchPerf())} disabled={loading}
              className="p-1.5 rounded-md hover:bg-surface text-dimmed disabled:opacity-30"
              title="刷新"><RefreshCw size={14} className={loading ? 'animate-spin' : ''} /></button>
            <button onClick={handleCopy}
              className="p-1.5 rounded-md hover:bg-surface text-dimmed"
              title={tab === 'logs' ? '复制日志' : '复制性能数据（Markdown）'}><Copy size={14} /></button>
            <button onClick={onClose}
              className="p-1.5 rounded-md hover:bg-surface text-dimmed" title="关闭"><X size={14} /></button>
          </div>
        </header>

        {tab === 'logs' ? (
          /* 日志列表 —— 底色走 token（此前硬编码 #1E1E1E）：
             级别色 `text-error/warning/success` 与正文 `text-disabled` **全是主题 token**，
             浅色主题下它们是深色 —— 配一块写死的深黑底必然看不清。
             「终端就该是黑的」在这里不成立：字不是终端色，底也不该自己定。 */
          <div ref={scrollRef} className="flex-1 overflow-auto bg-surface p-3 font-mono text-xs leading-relaxed">
            {logs.length === 0 ? (
              <div className="text-dimmed italic pt-4 text-center">
                {errorCount > 1 ? '日志加载失败，请检查后端是否运行' : '暂无日志'}
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
        ) : (
          /* 性能打点 —— 只呈现，不给「慢 / 不慢」下判断（阈值该由看数据的人定，
             见 backend/app/core/perf.py 的模块 docstring）。故这里刻意**不做颜色分级**：
             染红某个数字就是在替用户决定「多少算慢」。 */
          <div className="flex-1 overflow-auto bg-surface p-3">
            {perfError ? (
              <div className="text-dimmed italic pt-4 text-center">性能数据加载失败，请检查后端是否运行</div>
            ) : !perf ? (
              <div className="text-dimmed italic pt-4 text-center">加载中…</div>
            ) : perf.groups.length === 0 ? (
              <div className="text-dimmed italic pt-4 text-center">
                暂无打点数据 —— 后端刚启动，或还没有请求进来
              </div>
            ) : (
              <table className="w-full font-mono text-xs" data-perf-table>
                <thead>
                  <tr className="text-dimmed">
                    <th className="text-left font-normal pb-1.5">打点</th>
                    <th className="text-right font-normal pb-1.5 pl-2">次数</th>
                    <th className="text-right font-normal pb-1.5 pl-2">p50</th>
                    <th className="text-right font-normal pb-1.5 pl-2">p95</th>
                    <th className="text-right font-normal pb-1.5 pl-2">max</th>
                  </tr>
                </thead>
                <tbody>
                  {perf.groups.map(g => (
                    <tr key={g.name} className="hover:bg-elevated/5">
                      <td className="py-0.5 pr-2 break-all text-disabled">{g.name}</td>
                      <td className="py-0.5 pl-2 text-right text-dimmed tabular-nums">{g.count}</td>
                      <td className="py-0.5 pl-2 text-right tabular-nums">{fmtMs(g.p50)}</td>
                      <td className="py-0.5 pl-2 text-right tabular-nums">{fmtMs(g.p95)}</td>
                      <td className="py-0.5 pl-2 text-right tabular-nums">{fmtMs(g.max)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {perf && perf.groups.some(g => g.name.endsWith('[ttfb]')) && (
              <p className="mt-3 text-[11px] text-dimmed leading-relaxed">
                带 <span className="font-mono">[ttfb]</span> 的是流式端点，量到的是建连时间而非流总时长。
              </p>
            )}
          </div>
        )}

        {/* 底部 */}
        <footer className="border-t px-4 py-2 text-xs text-disabled shrink-0 flex items-center justify-between">
          {tab === 'logs' ? (
            <>
              <span>最近 {logs.length} 行 · 每 3 秒自动刷新</span>
              <span>日志存于内存，重启后清空</span>
            </>
          ) : (
            <>
              <span>共 {perf?.totalSamples ?? 0} 条样本 · 每 3 秒刷新</span>
              <span title={perf?.logFile ?? undefined}>
                {perf?.logFile ? `完整留档 perf.log · 保留 ${perf.keepDays} 天` : '落盘不可用（仅进程内统计）'}
              </span>
            </>
          )}
        </footer>
      </aside>
    </>
  )
}
