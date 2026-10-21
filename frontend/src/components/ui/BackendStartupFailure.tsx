import { useState } from 'react'
import { AlertCircle, FolderOpen, RotateCw, ArrowRight, Copy, Check, TriangleAlert } from 'lucide-react'
import {
  logDisplayText, failureDetailLines, installPathWarning, diagnosticsText,
  type StartupFailure,
} from '../../lib/startupFailure'

/**
 * 后端启动失败屏 —— 冷启动时后端没能起来，且**已经确定救不回来**（进程已退出）时显示。
 *
 * ⚠️ 与「正在启动后端服务…」加载屏的判别条件是 `alive`（见 `isFatalStartupFailure`），
 * 不是「有没有收到失败事件」：进程还活着只是慢，仍然自动放行（用户至少能进界面）；
 * 进程已经退了，放行只会换来满屏 ERR_CONNECTION_REFUSED，而原因就在这一屏上。
 *
 * ⚠️ 这里是**极少数必须给「日志路径」的用户可见位置**：多数故障（缺 DLL / 缺运行库 /
 * 杀软拦截 / 端口占用）在后端看来只是一次退出，只有 stderr 或退出码能说清原因。
 */
interface Props {
  failure: StartupFailure
  /** 「重试启动」调用成功（进程已重新拉起）后通知父组件回到加载屏 */
  onRetryStarted: () => void
  /** 重试**又失败**时带上新诊断 —— 否则屏幕仍是上一次的结论，看起来像「没反应」 */
  onFailureUpdate: (f: StartupFailure) => void
  /** 「仍然继续」—— 明知后端没起来也进入主界面（保留改造前的放行行为） */
  onContinue: () => void
}

export function BackendStartupFailure({ failure, onRetryStarted, onFailureUpdate, onContinue }: Props) {
  const [retrying, setRetrying] = useState(false)
  const [retryError, setRetryError] = useState('')
  const [copied, setCopied] = useState(false)

  // 安装路径问题可能与本次故障无关（也可能正是原因）—— 单独一条、措辞谨慎，
  // 不把它写成结论。⚠️ 它不看顶栏横幅的「已关闭」记忆：那里关掉的是
  // 「每次开机提醒一次」，这里是解释当前失败。
  const pathWarning = installPathWarning(failure)
  const detailLines = failureDetailLines(failure)

  const retry = async () => {
    setRetrying(true)
    setRetryError('')
    try {
      const { invoke } = await import('@tauri-apps/api/core')
      await invoke('restart_backend_sidecar')
      onRetryStarted()
    } catch (e) {
      setRetryError(e instanceof Error ? e.message : String(e))
      // 重试本身也失败了：拉一次新诊断，让屏幕显示这一次的结论。
      // ⚠️ 不这么做的话，用户点了重试、看到一行红字、而下面整块还是上一次的
      // 退出码和日志 —— 看起来像「重试没生效」，实际是「又失败了一次」。
      try {
        const { invoke } = await import('@tauri-apps/api/core')
        const fresh = await invoke<StartupFailure | null>('get_backend_startup_failure')
        if (fresh) onFailureUpdate(fresh)
      } catch { /* 拉不到就保留旧的：有总比没有好 */ }
    } finally {
      setRetrying(false)
    }
  }

  const openLogs = async () => {
    try {
      const { invoke } = await import('@tauri-apps/api/core')
      await invoke('open_log_dir')
    } catch (e) {
      setRetryError(e instanceof Error ? e.message : String(e))
    }
  }

  const copyDiagnostics = async () => {
    try {
      await navigator.clipboard.writeText(diagnosticsText(failure))
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    } catch (e) {
      setRetryError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className="h-screen flex flex-col items-center justify-center gap-4 bg-surface p-6">
      <div className="w-full max-w-[720px] rounded-xl border border-border-default bg-elevated p-5 flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <AlertCircle size={18} className="text-error shrink-0" />
          <span className="text-[15px] font-semibold text-foreground">后端服务没能启动</span>
        </div>

        <div className="text-[13px] text-dimmed">{failure.summary}</div>

        <div className="text-[12px] text-muted flex flex-col gap-0.5">
          {detailLines.map(line => (
            <span key={line} className="font-mono break-all">{line}</span>
          ))}
        </div>

        {pathWarning && (
          <div className="flex items-start gap-1.5 rounded-lg border border-border-light bg-surface px-2.5 py-2 text-[12px] text-muted">
            <TriangleAlert size={13} className="mt-0.5 shrink-0" />
            <span>{pathWarning}</span>
          </div>
        )}

        <div className="flex flex-col gap-1">
          <span className="text-[12px] font-medium text-muted">启动过程日志（末尾）</span>
          <pre className="max-h-[240px] overflow-auto rounded-lg border border-border-light bg-surface p-3 text-[11.5px] leading-[1.5] font-mono whitespace-pre-wrap break-all text-dimmed">
            {logDisplayText(failure)}
          </pre>
        </div>

        {retryError && (
          <div className="text-[12px] text-error">{retryError}</div>
        )}

        <div className="flex flex-wrap items-center gap-2 pt-1">
          <button
            onClick={retry}
            disabled={retrying}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border-default hover:bg-surface-hover disabled:opacity-50 text-[13px] font-medium text-foreground"
          >
            <RotateCw size={14} className={retrying ? 'animate-spin' : ''} />
            {retrying ? '正在重启…' : '重试启动'}
          </button>
          <button
            onClick={openLogs}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border-default hover:bg-surface-hover text-[13px] font-medium text-foreground"
          >
            <FolderOpen size={14} />
            打开日志文件夹
          </button>
          <button
            onClick={copyDiagnostics}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border-default hover:bg-surface-hover text-[13px] font-medium text-foreground"
          >
            {copied ? <Check size={14} /> : <Copy size={14} />}
            {copied ? '已复制' : '复制诊断信息'}
          </button>
          <div className="flex-1" />
          <button
            onClick={onContinue}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg hover:bg-surface-hover text-[13px] text-muted hover:text-dimmed"
          >
            仍然继续
            <ArrowRight size={14} />
          </button>
        </div>
      </div>

      <div className="text-[12px] text-muted text-center max-w-[560px]">
        点「复制诊断信息」把上面整块发给我们最省事；
        <span className="font-mono">limo_startup.log</span> 与
        <span className="font-mono"> limo_backend_stderr.log</span> 里是完整记录。
        <br />
        日志不会被「重试启动」清掉 —— 每次尝试都追加在后面。
      </div>
    </div>
  )
}
