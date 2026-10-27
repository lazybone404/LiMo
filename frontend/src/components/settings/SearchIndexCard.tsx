/**
 * 设置 → 通用 → 检索索引。
 *
 * **这张卡存在的理由**：全文索引库坏掉时后端**不再**炸启动（文档照样能读，
 * 只是搜不到），而「搜不到」与「库里没有」在界面上逐字相同 —— 用户永远不知道
 * 自己该点一下重建。所以这里必须同时给出「状态」与「出路」。
 *
 * ⚠️ 重建是**用户主动**触发的（后端那次 `rebuild` 带 `vacuum=True`，会重写整个
 * `search.db`）：启动路径不 VACUUM —— 那会让每次「打开应用」多等一次全库重写。
 * 代价在这里已被告知，故要一次二次确认。
 *
 * ⚠️ 状态**只在打开这张卡时取一次 + 重建后刷新**，不做轮询：索引的可用性不是
 * 实时变化的东西（它要么一开始就坏了，要么被手动重建修好）。
 */
import { useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle, Cpu, Database, Loader2, RefreshCw, Sparkles, Zap } from 'lucide-react'
import {
  getSearchStatus,
  rebuildVectorIndex,
  reindexSearch,
  setVectorSearch,
  syncVectorIndex,
  type SearchStatus,
  type VectorStatus,
} from '../../api/search'
import { setSearchHealth } from '../../lib/search-health'
import { useStore } from '../../store'

/** 字节数 → 人类可读（1 位小数，够用且不抖动）。 */
function fmtBytes(n: number): string {
  if (!n) return '—'
  const units = ['B', 'KB', 'MB', 'GB']
  let v = n
  let i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`
}

/** 本地时间 ISO → 「2026-10-26 17:31」。只在本进程内有效（重启后为空）。 */
function fmtTime(iso: string | null): string {
  if (!iso) return '本次启动后尚未重建'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

/** 向量侧一行状态里最需要被看见的那个数（优先级：失败 > 待索引 > 已索引）。 */
function vectorSummary(v: VectorStatus): string {
  const { total, ok, pending, failed } = v.docs
  const parts = [`已索引 ${ok}/${total} 篇`, `${v.chunks} 块`, fmtBytes(v.sizeBytes)]
  if (pending) parts.push(`待索引 ${pending}`)
  // `failed` 单独强调：其中 `fail_count >= MAX_FAIL` 的篇**不会再自动重试**（worker 的
  // 失败闸门），混进「待索引」里会让人一直等一个不会自己好的东西；未到上限的仍会被重试
  // （`auto` 档下一次心跳、`manual` 档点「立即建立索引」）。⚠️ 本函数拿不到当前档位，
  // 也不区分这两类 —— 故这里只报数、不下「会不会重试」的结论（`docs.failed` 是全部失败合计）。
  if (failed) parts.push(`失败 ${failed}`)
  return parts.join(' · ')
}

export function SearchIndexCard() {
  const [status, setStatus] = useState<SearchStatus | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  // 向量侧三个状态**各管各的**：开关动作、重建动作、重建二次确认。
  // 与字面侧共用 `busy` 会让「正在重建向量」把「重建字面索引」也锁住，反之亦然 ——
  // 两个动作的成本差着四个数量级，锁在一起只会让人以为自己点错了。
  const [vBusy, setVBusy] = useState(false)
  const [vConfirming, setVConfirming] = useState(false)
  const [vError, setVError] = useState<string | null>(null)
  // 三档释放策略的 label / hint **由后端下发**（`Capabilities.releasePolicies`）。
  // ⚠️ 前端不存第二份文案：`VectorWorker.maybe_release` 按 key 分派，加一档要改后端 ——
  // 前端再存一份的话，加档时设置页永远看不到它（用户选不到就等于没加）。
  // ⚠️ 空数组 = 「问不到」（capabilities 还没加载 / 老后端）→ 整组按钮不渲染。
  // 与 `lib/search-health` 的 `null` 同款判据：问不到不等于坏了，也不该摆一排出点不动的按钮。
  // ⚠️ selector 只取稳定引用的 `capabilities`，派生在外部做 —— 否则每次渲染都返回新数组。
  const releaseOptions = useStore(s => s.capabilities)?.releasePolicies ?? []
  // 触发方式两档同理走下发的 `triggerPolicies`（空数组 = 问不到 → 整组不渲染）。
  const triggerOptions = useStore(s => s.capabilities)?.triggerPolicies ?? []

  const vec = status?.vector

  // 攒批阈值的输入草稿。**必须本地暂存**：直接受控到 `vec.threshold` 的话，
  // 每敲一个数字都会发一次请求（后端每次都要跑一次 `sync_vector_runtime`），
  // 而输入框还来不及显示用户敲的第二位。提交时机 = 失焦 / 回车。
  const [thresholdDraft, setThresholdDraft] = useState('')
  // 后端值变了（首次加载 or 提交成功）就同步草稿；⚠️ 依赖写成 `vec?.threshold`
  // 这个**数值**而不是 `vec` 对象 —— 每次 status 更新都是新对象，会让它不停重置，
  // 用户正在输入的内容被抹掉。
  useEffect(() => {
    if (vec?.threshold !== undefined) setThresholdDraft(String(vec.threshold))
  }, [vec?.threshold])

  useEffect(() => {
    let alive = true
    getSearchStatus()
      .then(s => { if (alive) setStatus(s) })
      .catch(e => { if (alive) setLoadError(e instanceof Error ? e.message : String(e)) })
    return () => { alive = false }
  }, [])

  async function runReindex() {
    setBusy(true)
    setActionError(null)
    try {
      const next = await reindexSearch()
      setStatus(next)
      // 同一次重建的结果也写回共享状态：文档库顶部那条横幅据此立刻消失
      // （否则用户修完了横幅还在，会以为没修好）。
      setSearchHealth(next)
      setConfirming(false)
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  /** 开关 / 重建共用：动作返回的是**动作之后**的向量状态，就地换掉那一段。
   *  ⚠️ 不写 `setSearchHealth`：共享健康状态（文档库顶部横幅）只关心字面索引的
   *  `available`，向量开关不影响它 —— 多写一次就是给「横幅什么时候该消失」再加一个来源。 */
  async function runVector(action: () => Promise<{ status: VectorStatus }>) {
    setVBusy(true)
    setVError(null)
    try {
      const r = await action()
      setStatus(s => (s ? { ...s, vector: r.status } : s))
      setVConfirming(false)
    } catch (e) {
      setVError(e instanceof Error ? e.message : String(e))
    } finally {
      setVBusy(false)
    }
  }

  /** 攒批阈值的提交点（失焦 / 回车）。**非法输入回弹到后端值**而不是报错：
   *  用户想改的是「几篇」，敲进一个字母并不是他要表达的意思；反过来若把空值当 1
   *  提交，「清空重输」的中途状态就会被悄悄写进配置。两者都不是显式化能帮上忙的场景。 */
  function commitThreshold() {
    if (!vec) return
    const n = Number.parseInt(thresholdDraft, 10)
    if (!Number.isFinite(n) || n < 1) { setThresholdDraft(String(vec.threshold)); return }
    if (n === vec.threshold) return
    runVector(() => setVectorSearch(vec.enabled, undefined, undefined, undefined, n))
  }

  const unavailable = status ? !status.available : false
  // 可选的向量化引擎（云端 / 本地）。`current` 决定成本文案走哪一套 ——
  // ⚠️ 按 `local` 字段分支，**不按名字前缀猜**（改名即失效，且猜错的方向是
  // 「把免费的本地模型说成要花钱」）。
  const models = vec?.models ?? []
  const current = models.find(m => m.name === vec?.model)
  // 「你选中的这个引擎此刻用不了」必须**不点下拉也能看见**。
  // ⚠️ 只在 `vec.reason` 缺席时回落到引擎自己的 reason：开关开着却起不来时，
  // `sync_vector_runtime` 的 reason **就是** precheck 的原话（它在 `ensure_identity`
  // 之前），两处同时渲染等于同一句话连说两遍。
  const notReady = vec?.reason ?? (current && !current.ready ? current.reason : null)

  return (
    <div className="bg-elevated rounded-xl border border-border-light p-6 space-y-4" data-search-index-card>
      <p className="text-[13px] text-muted">
        文档库的全文检索依赖这份索引：它把每篇的正文与最新版笔记一起建索引，重建后能立刻搜到新内容。
      </p>

      {loadError && (
        <p className="text-[12px] text-warning flex items-start gap-2">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          读取索引状态失败：{loadError}
        </p>
      )}

      {status && (
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-[14px]">
            {unavailable
              ? <AlertTriangle size={16} className="text-warning shrink-0" />
              : <CheckCircle size={16} className="text-accent shrink-0" />}
            <span className={unavailable ? 'text-warning font-medium' : 'font-medium'}>
              {unavailable ? '索引不可用' : '索引正常'}
            </span>
          </div>

          {unavailable && status.lastError && (
            <p className="text-[12px] text-muted leading-relaxed">{status.lastError}</p>
          )}

          <div className="flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-muted">
            <span className="inline-flex items-center gap-1.5">
              <Database size={12} />{status.docCount} 篇 · {fmtBytes(status.sizeBytes)}
            </span>
            <span>上次重建：{fmtTime(status.rebuiltAt)}</span>
          </div>
        </div>
      )}

      {actionError && (
        <p className="text-[12px] text-warning flex items-start gap-2">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          {actionError}
        </p>
      )}

      {confirming ? (
        <div className="rounded-lg border border-border-default p-3 space-y-2">
          <p className="text-[12px] text-muted leading-relaxed">
            重建会从头扫一遍全部文档并重写索引文件，期间搜索可能短暂不可用；
            文档本身不会被改动。确定现在重建？
          </p>
          <div className="flex gap-2">
            <button onClick={runReindex} disabled={busy}
              className="px-3 py-1.5 text-[12px] rounded-lg bg-accent text-on-accent hover:bg-accent-hover disabled:opacity-50 inline-flex items-center gap-1.5">
              {busy && <Loader2 size={12} className="animate-spin" />}开始重建
            </button>
            <button onClick={() => setConfirming(false)} disabled={busy}
              className="px-3 py-1.5 text-[12px] rounded-lg border border-border-default disabled:opacity-50">
              取消
            </button>
          </div>
        </div>
      ) : (
        <button onClick={() => { setActionError(null); setConfirming(true) }} disabled={busy || !status}
          className="px-3 py-1.5 text-[12px] rounded-lg border border-border-default inline-flex items-center gap-1.5 disabled:opacity-50">
          {busy ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
          重建索引
        </button>
      )}

      {/* ── 语义检索（向量索引） ──
          与上面那段是**两套独立的库**：字面索引免费、毫秒级；向量索引要调
          embedding API、首次全库编码以分钟与分钱计。所以这里三件事都另外给：
          开关、状态、重建，且各自有自己的忙碌态。 */}
      {vec && (
        <div className="border-t border-border-light pt-4 space-y-3" data-vector-index>
          <div className="flex items-start justify-between gap-3">
            <div className="space-y-1">
              <div className="flex items-center gap-2 text-[14px]">
                <Cpu size={16} className="text-muted shrink-0" />
                <span className="font-medium">语义检索</span>
                <span className={
                  vec.enabled
                    ? 'text-[11px] px-1.5 py-0.5 rounded bg-accent/10 text-accent'
                    : 'text-[11px] px-1.5 py-0.5 rounded bg-elevated text-muted border border-border-light'
                }>
                  {vec.enabled ? '已开启' : '未开启'}
                </span>
              </div>
              <p className="text-[12px] text-muted leading-relaxed">
                开启后，检索会把字面结果与「意思相近」的结果融合排序 —— 一个词都没出现、
                但讲的是同一件事的文档也能被找出来。
              </p>
            </div>
            <button
              data-vector-toggle
              onClick={() => runVector(() => setVectorSearch(!vec.enabled))}
              disabled={vBusy || !vec.available}
              className="shrink-0 px-3 py-1.5 text-[12px] rounded-lg border border-border-default inline-flex items-center gap-1.5 disabled:opacity-50"
            >
              {vBusy ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
              {vec.enabled ? '关闭' : '开启'}
            </button>
          </div>

          {/* 引擎选择。⚠️ 切换**会因口径不符而停用**（云端 1024 维 ↔ 本地 512 维），
              需要重新建一次索引 —— 那句话由后端的 `reason` 给出，这里不另写一份。 */}
          {models.length > 0 && (
            <label className="flex items-center gap-2 text-[12px] text-muted" data-vector-model>
              <span className="shrink-0">向量化引擎</span>
              <select
                value={vec.model}
                disabled={vBusy}
                onChange={e => {
                  const name = e.target.value
                  if (name === vec.model) return
                  const opt = models.find(m => m.name === name)
                  // 未就绪的不发请求：切过去只会把一个**正在工作**的索引停掉，
                  // 而原因与「装了之后再来」的建议挡在这里说更直接。
                  if (opt && !opt.ready) {
                    setVError(`${opt.label} 当前不可用：${opt.reason ?? '未就绪'}`)
                    return
                  }
                  runVector(() => setVectorSearch(vec.enabled, name))
                }}
                className="flex-1 min-w-0 px-2 py-1 text-[12px] rounded-lg border border-border-default bg-surface disabled:opacity-50"
              >
                {models.map(m => (
                  <option key={m.name} value={m.name}>
                    {m.label}{m.ready || m.name === vec.model ? '' : '（未就绪）'}
                  </option>
                ))}
              </select>
            </label>
          )}

          {/* 成本与隐私必须在**开启之前**说清楚：云端要花钱、且会把正文发给第三方；
              本地则是「本机算、不上传、不计费」—— 两件事的措辞相反，**不能共用一段**。 */}
          <p className="text-[11px] text-muted leading-relaxed" data-vector-notice>
            {current?.local
              ? '开启后，文档的正文与最新版笔记只在这台电脑上编码 —— 内容不会上传、不产生费用，断网也能用。'
                + '首次会为全库建立索引（占用本机 CPU 一段时间），之后只有新增或改动的文档才重新编码。'
                + '索引本身存在本机，删除文档时对应的向量也会一并清掉。'
              : '开启后，文档的正文与最新版笔记会被发送到向量化模型并按字数计费；'
                + '首次会为全库建立索引，之后只有新增或改动的文档才重新编码。'
                + '索引本身存在本机，删除文档时对应的向量也会一并清掉。'}
          </p>

          {/* 模型内存（只在**本地**引擎下渲染）。云端引擎的「模型」在服务端，本机
              一个字节都不占 —— 把三档摆给它看，用户会以为某个按钮能省下点什么。
              ⚠️ 中间那档是默认，故 `vec.release` 缺失（老后端）时按 `idle` 显示。
              ⚠️ **档位清单来自 capabilities**（不是本地常量），取不到时整组不渲染 ——
              见组件顶部 `releaseOptions` 的注释。 */}
          {current?.local && releaseOptions.length > 0 && (() => {
            const release = vec.release ?? 'idle'
            const hint = releaseOptions.find(o => o.key === release)?.hint
            return (
              <div className="space-y-1.5" data-vector-release>
                <div className="flex flex-wrap items-center gap-2 text-[12px] text-muted">
                  <span className="shrink-0">模型内存</span>
                  <div className="flex gap-1">
                    {releaseOptions.map(o => (
                      <button
                        key={o.key}
                        data-release-option={o.key}
                        aria-pressed={release === o.key}
                        disabled={vBusy}
                        onClick={() => {
                          if (release === o.key) return
                          runVector(() => setVectorSearch(vec.enabled, undefined, o.key))
                        }}
                        className={release === o.key
                          ? 'px-2 py-0.5 text-[11px] rounded-md bg-accent text-on-accent disabled:opacity-50'
                          : 'px-2 py-0.5 text-[11px] rounded-md border border-border-default disabled:opacity-50'}
                      >
                        {o.label}
                      </button>
                    ))}
                  </div>
                </div>
                {hint && <p className="text-[11px] text-muted leading-relaxed">{hint}</p>}
              </div>
            )
          })()}

          {/* ── 建索引时机（2026-10-27）──
              两档 + 一个阈值。判据是「模型只为一批加载一次」：本地 bge-small 加载要
              3–5 秒、常驻约 90MB，为一两篇反复「载入 → 编码 → 释放」是纯浪费。
              ⚠️ 阈值输入框只在 `auto` 档渲染 —— `manual` 档下它没有任何作用，摆着会
              让人以为「攒够 N 篇还是会自动跑」，而那正是 `manual` 承诺不会发生的事。
              ⚠️ 档位清单与文案来自 capabilities，本地不存第二份（同 `模型内存`）。 */}
          {triggerOptions.length > 0 && (() => {
            const trigger = vec.trigger ?? 'auto'
            const hint = triggerOptions.find(o => o.key === trigger)?.hint
            return (
              <div className="space-y-1.5" data-vector-trigger>
                <div className="flex flex-wrap items-center gap-2 text-[12px] text-muted">
                  <span className="shrink-0">建索引时机</span>
                  <div className="flex gap-1">
                    {triggerOptions.map(o => (
                      <button
                        key={o.key}
                        data-trigger-option={o.key}
                        aria-pressed={trigger === o.key}
                        disabled={vBusy}
                        onClick={() => {
                          if (trigger === o.key) return
                          runVector(() => setVectorSearch(
                            vec.enabled, undefined, undefined, o.key))
                        }}
                        className={trigger === o.key
                          ? 'px-2 py-0.5 text-[11px] rounded-md bg-accent text-on-accent disabled:opacity-50'
                          : 'px-2 py-0.5 text-[11px] rounded-md border border-border-default disabled:opacity-50'}
                      >
                        {o.label}
                      </button>
                    ))}
                  </div>
                  {trigger === 'auto' && (
                    <label className="inline-flex items-center gap-1">
                      <span>攒够</span>
                      <input
                        type="number" min={1} step={1}
                        data-vector-threshold
                        value={thresholdDraft}
                        disabled={vBusy}
                        onChange={e => setThresholdDraft(e.target.value)}
                        onBlur={commitThreshold}
                        onKeyDown={e => { if (e.key === 'Enter') e.currentTarget.blur() }}
                        className="w-14 px-1.5 py-0.5 text-[11px] rounded-md border border-border-default bg-surface disabled:opacity-50"
                      />
                      <span>篇</span>
                    </label>
                  )}
                </div>
                {hint && <p className="text-[11px] text-muted leading-relaxed">{hint}</p>}
              </div>
            )
          })()}

          {notReady && (
            <p className="text-[12px] text-warning flex items-start gap-2" data-vector-requirement>
              <AlertTriangle size={14} className="mt-0.5 shrink-0" />
              {notReady}
            </p>
          )}

          {vec.lastError && (
            <p className="text-[12px] text-warning flex items-start gap-2">
              <AlertTriangle size={14} className="mt-0.5 shrink-0" />
              向量索引读写失败：{vec.lastError}
            </p>
          )}

          <div className="flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-muted">
            <span>{vectorSummary(vec)}</span>
            <span>
              模型口径：
              {vec.identity
                ? `${vec.identity.modelId} · ${vec.identity.dimension} 维 · ${vec.identity.chunkSize} 字/块`
                : '尚未建立'}
            </span>
          </div>

          {/* 立即建立：`auto` 档下是「不想等攒够」的加速器，`manual` 档下是**唯一**通路。
              与下面的「重建向量索引」是两件事 —— 这个只补还没建的部分（已建过的块
              hash 全中，一次编码调用都不发），那个清空重来、重花一遍全库的钱。 */}
          <div className="flex flex-wrap items-center gap-2" data-vector-sync>
            <button
              onClick={() => runVector(syncVectorIndex)}
              disabled={vBusy || !vec.enabled}
              title={vec.enabled ? undefined : '先开启语义检索'}
              className="px-3 py-1.5 text-[12px] rounded-lg border border-border-default inline-flex items-center gap-1.5 disabled:opacity-50"
            >
              {vBusy ? <Loader2 size={12} className="animate-spin" /> : <Zap size={12} />}
              立即建立索引
            </button>
            {vec.docs.pending > 0 && (
              <span className="text-[11px] text-muted">待索引 {vec.docs.pending} 篇</span>
            )}
          </div>

          {vError && (
            <p className="text-[12px] text-warning flex items-start gap-2">
              <AlertTriangle size={14} className="mt-0.5 shrink-0" />
              {vError}
            </p>
          )}

          {vConfirming ? (
            <div className="rounded-lg border border-border-default p-3 space-y-2" data-vector-rebuild-confirm>
              <p className="text-[12px] text-muted leading-relaxed">
                重建会清空现有向量并<strong className="font-medium">重新编码全库</strong> ——
                {/* 成本文案与上面那条 notice 同源（都按引擎的 `local` 分支）。
                    本地引擎下写「计费」是**错话**：它一分钱不花，只是占本机 CPU。 */}
                {current?.local
                  ? '全程在本机跑，不产生费用，但要花上一段时间（与首次建立索引相当）。'
                  : '这会产生一次完整的计费（与首次建立索引相当）。'}
                文档本身不会被改动。确定重建？
              </p>
              <div className="flex gap-2">
                <button onClick={() => runVector(rebuildVectorIndex)} disabled={vBusy}
                  className="px-3 py-1.5 text-[12px] rounded-lg bg-accent text-on-accent hover:bg-accent-hover disabled:opacity-50 inline-flex items-center gap-1.5">
                  {vBusy && <Loader2 size={12} className="animate-spin" />}开始重建
                </button>
                <button onClick={() => setVConfirming(false)} disabled={vBusy}
                  className="px-3 py-1.5 text-[12px] rounded-lg border border-border-default disabled:opacity-50">
                  取消
                </button>
              </div>
            </div>
          ) : (
            <button
              data-vector-rebuild
              onClick={() => { setVError(null); setVConfirming(true) }}
              disabled={vBusy || !vec.available || !vec.identity}
              title={vec.identity ? undefined : '先开启语义检索，索引建立后才有可重建的内容'}
              className="px-3 py-1.5 text-[12px] rounded-lg border border-border-default inline-flex items-center gap-1.5 disabled:opacity-50"
            >
              {vBusy ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
              重建向量索引
            </button>
          )}
        </div>
      )}
    </div>
  )
}
