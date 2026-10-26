/**
 * 文档库顶部的「检索索引不可用」横幅。
 *
 * 与设置页的 `SearchIndexCard` **分工不同**：那张卡是「用户想了解/维护索引」时去的地方，
 * 这张横幅是「索引已经坏了、用户还没意识到」时的**推送**。两者共用同一份
 * `refreshSearchHealth` / `reindexSearch`，判据只有一处（`status.available`）。
 *
 * ⚠️ **只在 `available === false` 时渲染**：`null`（还没取到 / 取不到）一律不渲染 ——
 * 后端没起的那几十秒里挂一条「去重建索引」，按钮是点不动的，只会制造一次误判。
 *
 * ⚠️ **关闭是「本次挂载内」的**：`dismissed` 是组件本地 state，面板折叠会卸载它
 * （`SlotLayout` 是 `collapsed ? <PanelStrip/> : <Comp/>`），再打开会重新提示一次。
 * 这是有意的 —— 索引坏着而界面只字不提，正是这条横幅要治的病。
 */
import { useState } from 'react'
import { AlertTriangle, Loader2, RefreshCw, X } from 'lucide-react'
import { reindexSearch } from '../../api/search'
import { setSearchHealth, useSearchHealth } from '../../lib/search-health'

export function SearchIndexBanner() {
  const status = useSearchHealth()
  const [dismissed, setDismissed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!status || status.available || dismissed) return null

  async function runReindex() {
    setBusy(true)
    setError(null)
    try {
      // 重建的返回值就是新状态 —— 直接写回，横幅随 available=true 立刻消失
      setSearchHealth(await reindexSearch())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mx-3 mt-2 mb-1 rounded-lg bg-warning/10 px-3 py-2 text-[11px] text-warning leading-relaxed">
      <div className="flex items-start gap-1.5">
        <AlertTriangle size={12} className="mt-0.5 shrink-0" />
        <div className="flex-1 min-w-0">
          <span className="font-medium">全文检索不可用</span>
          {status.lastError && <span className="opacity-80"> · {status.lastError}</span>}
        </div>
        <button onClick={() => setDismissed(true)} title="本次不再提示"
          className="shrink-0 opacity-60 hover:opacity-100">
          <X size={12} />
        </button>
      </div>

      {error && <p className="mt-1 opacity-80">重建失败：{error}</p>}

      <button onClick={runReindex} disabled={busy}
        className="mt-1.5 inline-flex items-center gap-1 rounded-md border border-warning/40 px-2 py-0.5 hover:bg-warning/10 disabled:opacity-50">
        {busy ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}
        重建索引
      </button>
    </div>
  )
}
