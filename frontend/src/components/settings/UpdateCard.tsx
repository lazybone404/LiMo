/**
 * 设置 → 通用 → 应用更新。
 *
 * **这张卡只做两件事**：把「有没有新版本」这件事变得看得见，以及让用户点一下就能装。
 * 检查、下载、验签、杀 sidecar、启安装器全在 Rust 侧（`src-tauri/src/updater.rs`），
 * 这里连一条重试判断都不写 —— 前端每多一处对流程的假设，就多一处与真实流程分叉的机会。
 *
 * ## 三条写在代码里的判据
 *
 * ① **有任务在跑时不装**。安装会让应用退出，正在跑的生成 / 转录 / 导入全部断在半路，
 *    而它们没有任何断点续传。所以这时按钮禁用并写明原因，不是默默点不动。
 *
 * ② **「下载并安装」要两步确认**。应用会重启、任务会中断，这是不可撤销的动作；
 *    卡片内联一个确认框，不用原生 `confirm`（它在 Tauri 里是阻塞式的，且样式不可控）。
 *
 * ③ **失败一律就地显示，不弹 toast**。这张卡的整块界面就是为「现在是什么状态」而存在的，
 *    把错误丢到屏幕角落的 toast 里，等于让用户盯着一个停在「可更新」状态的卡片猜。
 *
 * ⚠️ 装完之后**没有「成功」这一步**：Windows 上插件用 `std::process::exit(0)` 起安装器，
 * 进程直接没了。所以「正在安装，应用即将重启」是这条链路能给的最后一句话。
 */
import { useEffect, useRef, useState } from 'react'
import {
  AlertTriangle, ArrowUpRight, CheckCircle, Download, Loader2, RefreshCw,
} from 'lucide-react'
import {
  checkForUpdate, installUpdate, isDesktop, RELEASES_URL,
  type UpdateStatus,
} from '../../api/update'
import {
  clearSkipped, getUpdatePrefs, markChecked, skipVersion,
} from '../../lib/update-prefs'
import { useStore } from '../../store'

/** Tauri 的 `invoke` 失败时 reject 的是**字符串**（Rust 侧的 `Err(String)`），
 *  不是 `Error`。`getErrorMessage` 会把它截到 150 字，够不到「所有线路都失败了」
 *  后面那半句出路，所以这里按字符串原样透出。 */
function errText(e: unknown): string {
  if (typeof e === 'string') return e
  if (e instanceof Error) return e.message
  return String(e)
}

/** Release notes 只给一段摘要 —— 完整清单在 Releases 页面上，这里要的是「改了什么」。 */
const NOTES_LIMIT = 600

function notesSummary(notes: string | null): string {
  if (!notes) return ''
  const text = notes.trim()
  return text.length > NOTES_LIMIT ? text.slice(0, NOTES_LIMIT).trimEnd() + '…' : text
}

export function UpdateCard() {
  const desktop = isDesktop()
  const [checking, setChecking] = useState(false)
  const [status, setStatus] = useState<UpdateStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  // 两步确认：先点「下载并安装」，再点一次「开始安装」。
  const [confirming, setConfirming] = useState(false)
  // 装到一半的进度。`null` = 还没开始下载。
  const [progress, setProgress] = useState<{ downloaded: number; total: number | null; viaProxy: boolean } | null>(null)
  const [installing, setInstalling] = useState<string | null>(null)
  // 跳过的版本号。**state 而非直接读 localStorage**：本页点完要立刻重渲染。
  const [skipped, setSkipped] = useState(() => getUpdatePrefs().skippedVersion)
  // 安装是单向的：一旦开始，任何重新检查 / 重试入口都必须关掉，
  // 否则用户在一次安装进行中再点一下，会并发起第二个下载。
  const installStarted = progress !== null || installing !== null

  const anyTaskRunning = useStore(s =>
    Object.values(s.processingTasks).some(t => t.status === 'running'))

  const aliveRef = useRef(true)
  useEffect(() => () => { aliveRef.current = false }, [])

  async function runCheck() {
    setChecking(true)
    setError(null)
    setConfirming(false)
    try {
      const next = await checkForUpdate()
      if (!aliveRef.current) return
      setStatus(next)
      // 跳过记录可能是在别的会话里写的 —— 每次查完重读一次，
      // 免得「刚在另一处清掉了、这里还显示已跳过」。
      setSkipped(getUpdatePrefs().skippedVersion)
    } catch (e) {
      if (aliveRef.current) setError(errText(e))
    } finally {
      // ⚠️ 失败也记时间：不记的话，断网时每次启动都要完整跑一遍三条线路的超时，
      // 启动链被拖住而用户什么都得不到（见 lib/update-prefs.ts）。
      markChecked()
      if (aliveRef.current) setChecking(false)
    }
  }

  // 打开这张卡就查一次。手动点开的「通用」页面，用户想要的正是此刻的结论；
  // 而静默检查的 24 小时节流属于启动链（`App.tsx`），不是这里。
  // ⚠️ 用 ref 而非 state 当守卫：开发模式的 StrictMode 会把 effect 跑两遍。
  const autoRan = useRef(false)
  useEffect(() => {
    if (!desktop || autoRan.current) return
    autoRan.current = true
    void runCheck()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [desktop])

  function onSkip() {
    const v = status?.update?.version
    if (!v) return
    skipVersion(v)
    setSkipped(v)
    setConfirming(false)
  }

  function onUnskip() {
    clearSkipped()
    setSkipped('')
  }

  async function runInstall() {
    setConfirming(false)
    setError(null)
    setProgress({ downloaded: 0, total: null, viaProxy: false })
    try {
      await installUpdate(p => {
        if (!aliveRef.current) return
        if (p.kind === 'progress') {
          setProgress({ downloaded: p.downloaded, total: p.total, viaProxy: p.viaProxy })
        } else {
          setInstalling(p.version)
        }
      })
      // ⚠️ Windows 上压根走不到这里（进程已被 `exit(0)`）。非 Windows 分支在 Rust 侧
      // 调 `app.restart()`，同样不该执行到这一行。留着不改状态是对的：
      // 一旦在这里写「安装完成」，永远不会有第二次渲染把它抹掉。
    } catch (e) {
      if (!aliveRef.current) return
      setProgress(null)
      setInstalling(null)
      setError(errText(e))
    }
  }

  const update = status?.update ?? null
  const isSkipped = !!update && update.version === skipped
  const pct = progress && progress.total
    ? Math.min(100, Math.round((progress.downloaded / progress.total) * 100))
    : null

  // 非桌面版（浏览器里 `npm run dev`）—— 不渲染任何按钮，只说明为什么没有。
  if (!desktop) {
    return (
      <div className="bg-elevated rounded-xl border border-border-light p-6 space-y-3" data-update-card>
        <div className="flex items-center gap-2 text-[14px]">
          <Download size={16} className="text-muted shrink-0" />
          <span className="font-medium">应用更新</span>
        </div>
        <p className="text-[12px] text-muted leading-relaxed">
          在浏览器里打开时无法更新 —— 应用更新需要桌面版的安装环境（下载、验签、启动安装器）。
          用「检查更新」这类操作请打开桌面版 LiMo。
        </p>
      </div>
    )
  }

  return (
    <div className="bg-elevated rounded-xl border border-border-light p-6 space-y-4" data-update-card>
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-1">
          <div className="flex items-center gap-2 text-[14px]">
            <Download size={16} className="text-muted shrink-0" />
            <span className="font-medium">应用更新</span>
            {status && (
              <span className="text-[11px] text-muted">当前版本 v{status.currentVersion}</span>
            )}
          </div>
          <p className="text-[12px] text-muted leading-relaxed">
            从 GitHub 发布页检查并安装新版本。更新包会先验签再安装，应用会自动重启。
          </p>
        </div>
        <button
          onClick={runCheck}
          disabled={checking || installStarted}
          data-update-check
          className="shrink-0 px-3 py-1.5 text-[12px] rounded-lg border border-border-default inline-flex items-center gap-1.5 disabled:opacity-50"
        >
          {checking ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
          {checking ? '检查中' : '检查更新'}
        </button>
      </div>

      {/* 结果那一行。四种可能各说一句话，且**互斥**（不是叠加显示）：
          已是最新 / 有新版本 / 已跳过 / 检查失败。 */}
      {!checking && !error && status && !update && (
        <p className="text-[13px] flex items-center gap-2 text-muted" data-update-uptodate>
          <CheckCircle size={14} className="text-accent shrink-0" />
          已是最新版本
        </p>
      )}

      {!checking && update && (
        <div className="space-y-3" data-update-available>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[14px] font-medium">发现新版本 v{update.version}</span>
            {update.date && <span className="text-[11px] text-muted">{update.date.slice(0, 10)}</span>}
            {isSkipped && (
              <span className="text-[11px] px-1.5 py-0.5 rounded bg-elevated text-muted border border-border-light">
                已跳过
              </span>
            )}
          </div>

          {notesSummary(update.notes) && (
            <pre className="text-[12px] text-muted leading-relaxed whitespace-pre-wrap max-h-48 overflow-y-auto rounded-lg border border-border-light p-3 font-sans">
              {notesSummary(update.notes)}
            </pre>
          )}

          {/* 已跳过：只给「重新看看」与「去发布页」。不摆「下载并安装」——
              用户正是表达过「这次不要」，再把按钮摆在最显眼处等于没听。 */}
          {isSkipped ? (
            <div className="flex flex-wrap gap-2">
              <button onClick={onUnskip} data-update-unskip
                className="px-3 py-1.5 text-[12px] rounded-lg border border-border-default">
                取消跳过
              </button>
              <ReleasesLink />
            </div>
          ) : confirming ? (
            <div className="rounded-lg border border-border-default p-3 space-y-2" data-update-confirm>
              <p className="text-[12px] text-muted leading-relaxed">
                安装会<strong className="font-medium">关闭并重启 LiMo</strong>。
                {anyTaskRunning
                  ? '当前还有任务在运行，现在安装会让它们中断在半路 —— 按钮先禁用，等任务完成再点。'
                  : '如果此刻正在生成笔记或转录，它们会中断。'}
              </p>
              <div className="flex gap-2">
                <button onClick={runInstall} disabled={anyTaskRunning} data-update-install-start
                  className="px-3 py-1.5 text-[12px] rounded-lg bg-accent text-on-accent hover:bg-accent-hover disabled:opacity-50">
                  开始安装
                </button>
                <button onClick={() => setConfirming(false)}
                  className="px-3 py-1.5 text-[12px] rounded-lg border border-border-default">
                  取消
                </button>
              </div>
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <button
                onClick={() => { setError(null); setConfirming(true) }}
                disabled={installStarted}
                title={anyTaskRunning ? '有任务正在运行，完成后再更新' : undefined}
                data-update-install
                className="px-3 py-1.5 text-[12px] rounded-lg bg-accent text-on-accent hover:bg-accent-hover inline-flex items-center gap-1.5 disabled:opacity-50"
              >
                <Download size={12} />
                下载并安装
              </button>
              <button onClick={onSkip} disabled={installStarted} data-update-skip
                className="px-3 py-1.5 text-[12px] rounded-lg border border-border-default disabled:opacity-50">
                跳过此版本
              </button>
              {anyTaskRunning && (
                <span className="text-[11px] text-warning">有任务正在运行</span>
              )}
            </div>
          )}
        </div>
      )}

      {/* ── 下载 / 安装进度 ──
          进度条只在**下载**阶段有意义；安装阶段没有百分比可报（安装器自己在界面上画），
          所以那一段换成一句话。 */}
      {progress && (
        <div className="space-y-1.5" data-update-progress>
          <div className="flex items-center justify-between text-[12px] text-muted">
            <span className="inline-flex items-center gap-1.5">
              <Loader2 size={12} className="animate-spin" />
              {installing ? '正在安装，应用即将重启' : '正在下载更新包'}
            </span>
            {pct !== null && !installing && <span>{pct}%</span>}
          </div>
          {!installing && (
            <div className="h-1.5 rounded-full bg-surface-hover overflow-hidden">
              <div
                className="h-full bg-accent transition-all"
                // 总长度未知时给一条 30% 的固定宽度：`pct === null` 那条分支
                // 不是错误，是清单没给 `content-length`，进度条只能表达「在动」。
                style={{ width: pct === null ? '30%' : `${pct}%` }}
              />
            </div>
          )}
          {progress.viaProxy && !installing && (
            <p className="text-[11px] text-muted">直连不通，正在通过中转线路下载。</p>
          )}
        </div>
      )}

      {error && (
        <div className="space-y-1.5" data-update-error>
          <p className="text-[12px] text-warning flex items-start gap-2">
            <AlertTriangle size={14} className="mt-0.5 shrink-0" />
            {error}
          </p>
          <ReleasesLink />
        </div>
      )}
    </div>
  )
}

/** 「去发布页手动下载」—— 更新失败时唯一还有效的出路，所以每一处失败旁边都有它。
 *  ⚠️ 用**直连**地址（`RELEASES_URL`），不能带中转让前缀：中转只代理资源文件。 */
function ReleasesLink() {
  return (
    <a
      href={RELEASES_URL}
      target="_blank"
      rel="noreferrer"
      data-update-releases
      className="text-[12px] text-accent hover:underline inline-flex items-center gap-1"
    >
      在浏览器中打开 Releases
      <ArrowUpRight size={12} />
    </a>
  )
}
