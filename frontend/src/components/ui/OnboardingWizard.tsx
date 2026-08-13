/**
 * 首次启动向导 — 三屏引导流程
 * 屏 0：阅读氛围选择（4 张预设卡片）
 * 屏 1：系统组件检测（ffmpeg / SearXNG）
 *
 * 替代旧版 SetupWizard（仅系统组件检测的单屏弹窗）。
 */
import { useState, useEffect } from 'react'
import { Check, X, Download, ChevronRight, ChevronLeft, KeyRound, ShieldCheck, Loader2 } from 'lucide-react'
import { useStore } from '../../store'
import { THEME_PRESETS } from '../../lib/theme-presets'
import { fetchCapabilities, apiFetch, apiUrl } from '../../api/client'
import { updateProvider, testProviderConnection } from '../../api/settings'

interface ComponentStatus {
  name: string
  status: 'found' | 'missing' | 'bundled'
  path: string | null
}

interface Props {
  onDone: () => void
}

const COMPONENT_INFO: Record<string, { title: string; desc: string; downloadUrl: string }> = {
  ffmpeg: {
    title: 'FFmpeg（视频处理）',
    desc: '用于视频下载和音频提取。如未安装，视频导入功能不可用。',
    downloadUrl: 'https://ffmpeg.org/download.html',
  },
  searxng: {
    title: 'SearXNG（高质量搜索）',
    desc: '本地搜索引擎，提供更优质的搜索结果。未安装时自动降级为 DuckDuckGo。',
    downloadUrl: 'https://github.com/searxng/searxng/releases',
  },
}

/** 引导屏 0 展示的 4 个氛围预设（经典系列） */
const VIBE_PRESETS = THEME_PRESETS.filter(p =>
  ['shuxiang', 'jianjing', 'fugu', 'qingzhu'].includes(p.name),
)

export function OnboardingWizard({ onDone }: Props) {
  const [screen, setScreen] = useState(0)
  const applyPreset = useStore(s => s.applyPreset)
  const addToast = useStore(s => s.addToast)

  // ── 屏 2：AI 配置（基于 capabilities.missingKeys）──
  const caps = useStore(s => s.capabilities)
  const setCapabilities = useStore(s => s.setCapabilities)
  const missingLlm = caps?.missingKeys?.llm ?? []
  const missingEngine = caps?.missingKeys?.engine ?? []
  const hasMissing = missingLlm.length > 0 || missingEngine.length > 0
  const [llmKeys, setLlmKeys] = useState<Record<string, string>>({})
  const [dsEngineKey, setDsEngineKey] = useState('')
  const [savingId, setSavingId] = useState<string | null>(null)
  const [saveMsg, setSaveMsg] = useState<Record<string, string>>({})

  // 保存成功后重新拉 capabilities → missingKeys 实时更新（已配项从列表消失）
  const refreshMissing = () => {
    fetchCapabilities().then(setCapabilities).catch(() => {})
  }
  const handleSaveLlmKey = async (item: { id: string; name: string }) => {
    const key = (llmKeys[item.id] || '').trim()
    if (!key) { setSaveMsg(m => ({ ...m, [item.id]: '请输入 API Key' })); return }
    setSavingId(item.id)
    try {
      await updateProvider(item.id, { api_key: key })
      // 连通验证（失败不阻塞保存——可能是网络/代理问题，Key 本身已保存）
      let verified = true
      try { await testProviderConnection(item.id) } catch { verified = false }
      refreshMissing()
      setSaveMsg(m => ({ ...m, [item.id]: verified ? '已保存并验证通过 ✓' : '已保存，但连通验证失败（请检查 Key 或网络）' }))
    } catch (e: any) {
      setSaveMsg(m => ({ ...m, [item.id]: `保存失败: ${e?.message || e}` }))
    } finally { setSavingId(null) }
  }
  const handleSaveDsEngineKey = async () => {
    const key = dsEngineKey.trim()
    if (!key) { setSaveMsg(m => ({ ...m, 'dashscope-engine': '请输入 API Key' })); return }
    setSavingId('dashscope-engine')
    try {
      // PUT /engine-providers/dashscope 后端自动双向同步 → cloud.dashscope（has_dashscope 检测源）
      await apiFetch(apiUrl('/engine-providers/dashscope'), {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ api_key: key }),
      })
      refreshMissing()
      setSaveMsg(m => ({ ...m, 'dashscope-engine': '已保存 ✓' }))
    } catch (e: any) {
      setSaveMsg(m => ({ ...m, 'dashscope-engine': `保存失败: ${e?.message || e}` }))
    } finally { setSavingId(null) }
  }

  // ── 屏 1：系统组件检测 ──
  const [components, setComponents] = useState<ComponentStatus[]>([])
  const [detectLoading, setDetectLoading] = useState(true)

  useEffect(() => {
    if (screen !== 1) return
    const isTauri = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
    if (!isTauri) {
      setDetectLoading(false)
      return
    }
    import('@tauri-apps/api/core')
      .then(({ invoke }) => invoke<ComponentStatus[]>('detect_components'))
      .then(setComponents)
      .catch(() => {})
      .finally(() => setDetectLoading(false))
  }, [screen])

  const handlePresetClick = (name: string) => {
    applyPreset(name)
    addToast('氛围已应用')
  }

  // ── 屏 0：阅读氛围选择 ──
  if (screen === 0) {
    return (
      <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 backdrop-blur-sm">
        <div className="bg-elevated rounded-2xl shadow-xl border border-border-light w-[560px] max-h-[90vh] overflow-y-auto p-8 space-y-8">
          {/* 进度点 */}
          <div className="flex justify-center gap-2">
            <div className="w-2 h-2 rounded-full bg-accent" />
            <div className="w-2 h-2 rounded-full bg-border-default" />
            <div className="w-2 h-2 rounded-full bg-border-default" />
          </div>

          {/* 标题 */}
          <div className="text-center space-y-2">
            <h1 className="text-[22px] font-semibold">选择你喜欢的阅读氛围</h1>
            <p className="text-[14px] text-dimmed">之后可在设置中随时更改</p>
          </div>

          {/* 预设卡片 — tex-card 自驱动视觉预览（颜色/底色/纹理/字体） */}
          <div className="grid grid-cols-2 gap-3">
            {VIBE_PRESETS.map(p => (
              <button
                key={p.name}
                onClick={() => handlePresetClick(p.name)}
                className="text-left p-4 rounded-xl border border-border-light hover:border-accent hover:bg-accent/5 transition-all group"
                data-font={p.font}
              >
                {/* 视觉预览块：data-* 局部派生 CSS 变量 → 真实颜色/底色/纹理/字体 */}
                <div
                  className="tex-card rounded-lg p-3 mb-3 border border-border-light"
                  data-accent={p.accent}
                  data-base={p.base}
                  data-texture={p.texture}
                >
                  <div className="flex items-center gap-2">
                    <span className="w-4 h-4 rounded-full shrink-0" style={{ background: 'var(--tk-accent)' }} />
                    <span className="text-[13px] font-semibold" style={{ color: 'var(--tk-accent)' }}>{p.label}</span>
                    <span className="ml-auto w-5 h-5 rounded border border-black/10 shrink-0"
                      style={{ background: 'var(--tk-surface)' }} title="底色" />
                  </div>
                  <div className="text-[11px] mt-1.5 text-dimmed">字体预览 — 阅读体验示例</div>
                </div>
                {/* 文字描述：底色/字体/纹理 key */}
                <div className="text-[12px] text-dimmed space-y-0.5 ml-1">
                  <div className="flex gap-2">
                    <span className="text-disabled w-8 shrink-0">底色</span>
                    <span>{p.base}</span>
                  </div>
                  <div className="flex gap-2">
                    <span className="text-disabled w-8 shrink-0">字体</span>
                    <span>{p.font}</span>
                  </div>
                  <div className="flex gap-2">
                    <span className="text-disabled w-8 shrink-0">纹理</span>
                    <span>{p.texture}</span>
                  </div>
                </div>
              </button>
            ))}
          </div>

          {/* 跳过按钮 */}
          <button
            onClick={() => setScreen(1)}
            className="w-full py-2.5 rounded-xl text-[14px] text-dimmed hover:text-foreground hover:bg-surface-hover transition-colors"
          >
            跳过
          </button>
        </div>
      </div>
    )
  }

  // ── 屏 1：系统组件检测 ──
  if (detectLoading) {
    return (
      <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 backdrop-blur-sm">
        <div className="bg-elevated rounded-2xl shadow-xl border border-border-light w-[480px] p-8 text-center">
          <div className="flex justify-center gap-2 mb-6">
            <div className="w-2 h-2 rounded-full bg-border-default" />
            <div className="w-2 h-2 rounded-full bg-accent" />
            <div className="w-2 h-2 rounded-full bg-border-default" />
          </div>
          <div className="animate-pulse text-dimmed text-[15px]">正在检测系统环境…</div>
        </div>
      </div>
    )
  }

  // ── 屏 2：AI 配置（LLM / OCR·ASR 引擎 Key）──
  if (screen === 2) {
    return renderAiConfigScreen({
      loading: !caps,
      missingLlm,
      missingEngine,
      llmKeys,
      dsEngineKey,
      savingId,
      saveMsg,
      onLlmKeyChange: (id, v) => setLlmKeys(k => ({ ...k, [id]: v })),
      onDsKeyChange: setDsEngineKey,
      onSaveLlm: handleSaveLlmKey,
      onSaveDs: handleSaveDsEngineKey,
      onBack: () => setScreen(1),
      onDone,
    })
  }

  const allFound = components.length === 0 || components.every(c => c.status !== 'missing')

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 backdrop-blur-sm">
      <div className="bg-elevated rounded-2xl shadow-xl border border-border-light w-[480px] max-h-[90vh] overflow-y-auto p-8 space-y-6">
        {/* 进度点 */}
        <div className="flex justify-center gap-2">
          <div className="w-2 h-2 rounded-full bg-border-default" />
          <div className="w-2 h-2 rounded-full bg-accent" />
          <div className="w-2 h-2 rounded-full bg-border-default" />
        </div>

        {/* 头部 */}
        <div className="text-center space-y-2">
          <h1 className="text-[22px] font-semibold">系统环境检测</h1>
          <p className="text-[14px] text-dimmed leading-relaxed">
            检测可选系统组件，缺失的组件可在设置中补充安装。
          </p>
        </div>

        {/* 组件列表 */}
        {components.length === 0 ? (
          <p className="text-center text-[13px] text-dimmed py-4">所有组件已就绪，无需额外配置。</p>
        ) : (
          <div className="space-y-3">
            {components.map(c => {
              const info = COMPONENT_INFO[c.name]
              if (!info) return null
              const isOk = c.status !== 'missing'
              const statusLabel = c.status === 'bundled'
                ? '已内置' : c.status === 'found'
                ? (c.path || '已安装') : '未检测到'

              return (
                <div key={c.name}
                  className={`flex items-start gap-3 p-4 rounded-xl border transition-colors ${
                    isOk ? 'border-success/20 bg-success/5' : 'border-warning/20 bg-warning/5'
                  }`}>
                  <div className={`mt-0.5 shrink-0 rounded-full p-1.5 ${
                    isOk ? 'bg-success/10 text-success' : 'bg-warning/10 text-warning'
                  }`}>
                    {isOk ? <Check size={16} /> : <X size={16} />}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-[14px] font-medium">{info.title}</span>
                      <span className={`text-[11px] px-1.5 py-0.5 rounded-full font-medium ${
                        isOk ? 'bg-success/10 text-success' : 'bg-warning/10 text-warning'
                      }`}>{statusLabel}</span>
                    </div>
                    <p className="text-[12px] text-dimmed mt-1 leading-relaxed">{info.desc}</p>
                    {!isOk && (
                      <a href={info.downloadUrl} target="_blank" rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 mt-2 text-[12px] text-accent hover:underline">
                        <Download size={12} />获取安装指南
                      </a>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        )}

        {/* 底部 */}
        <div className="flex gap-2">
          <button onClick={() => setScreen(0)}
            className="flex-1 py-2.5 rounded-xl border border-border-light text-[14px] hover:bg-surface-hover transition-colors flex items-center justify-center gap-1">
            <ChevronLeft size={16} />
            上一步
          </button>
          <button
            onClick={() => (caps && !hasMissing ? onDone() : setScreen(2))}
            className="flex-[2] py-2.5 rounded-xl bg-accent text-white text-[14px] font-medium hover:bg-accent-hover transition-colors flex items-center justify-center gap-2">
            {caps && !hasMissing
              ? (allFound ? '开始使用' : '知道了，开始使用')
              : '下一步'}
            <ChevronRight size={16} />
          </button>
        </div>
      </div>
    </div>
  )
}

// ── 屏 2：AI 配置（LLM / OCR·ASR 引擎 Key）──
function renderAiConfigScreen(props: {
  loading: boolean  // capabilities 未加载完成
  missingLlm: { id: string; name: string }[]
  missingEngine: string[]
  llmKeys: Record<string, string>
  dsEngineKey: string
  savingId: string | null
  saveMsg: Record<string, string>
  onLlmKeyChange: (id: string, v: string) => void
  onDsKeyChange: (v: string) => void
  onSaveLlm: (item: { id: string; name: string }) => void
  onSaveDs: () => void
  onBack: () => void
  onDone: () => void
}) {
  const { loading, missingLlm, missingEngine, llmKeys, dsEngineKey, savingId, saveMsg,
    onLlmKeyChange, onDsKeyChange, onSaveLlm, onSaveDs, onBack, onDone } = props
  const isLoading = loading

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 backdrop-blur-sm">
      <div className="bg-elevated rounded-2xl shadow-xl border border-border-light w-[560px] max-h-[90vh] overflow-y-auto p-8 space-y-6">
        {/* 进度点 */}
        <div className="flex justify-center gap-2">
          <div className="w-2 h-2 rounded-full bg-border-default" />
          <div className="w-2 h-2 rounded-full bg-border-default" />
          <div className="w-2 h-2 rounded-full bg-accent" />
        </div>

        {/* 头部 */}
        <div className="text-center space-y-2">
          <h1 className="text-[22px] font-semibold">配置 AI 与引擎</h1>
          <p className="text-[14px] text-dimmed leading-relaxed">
            填入模型服务 API Key 即可开始使用。可在设置中随时更改。
          </p>
        </div>

        {isLoading ? (
          <div className="flex items-center justify-center gap-2 py-8 text-dimmed text-[14px]">
            <Loader2 size={16} className="animate-spin" />正在加载能力配置…
          </div>
        ) : (
          <>
            {/* ── LLM 模型 Key ── */}
            <div className="space-y-2">
              <div className="flex items-center gap-2 text-[13px] font-medium text-dimmed">
                <KeyRound size={14} />LLM 模型服务
              </div>
              {missingLlm.length === 0 ? (
                <div className="flex items-center gap-2 p-3 rounded-xl border border-success/20 bg-success/5 text-success text-[13px]">
                  <ShieldCheck size={16} />所有模型服务已配置
                </div>
              ) : (
                missingLlm.map(item => (
                  <div key={item.id} className="p-4 rounded-xl border border-border-light bg-surface space-y-2">
                    <div className="flex items-center gap-2">
                      <span className="text-[14px] font-medium">{item.name}</span>
                      <span className="text-[11px] px-1.5 py-0.5 rounded-full bg-warning/10 text-warning font-medium">未配置</span>
                      {item.id === item.name && (
                        <span className="text-[11px] text-disabled">自定义厂商，请到设置页添加</span>
                      )}
                    </div>
                    <div className="flex gap-2">
                      <input
                        type="password"
                        value={llmKeys[item.id] || ''}
                        onChange={e => onLlmKeyChange(item.id, e.target.value)}
                        placeholder={`输入 ${item.name} API Key`}
                        className="flex-1 px-3 py-2 rounded-lg border border-border-light bg-surface-hover text-[13px] focus:outline-none focus:border-accent"
                      />
                      <button
                        onClick={() => onSaveLlm(item)}
                        disabled={savingId === item.id}
                        className="px-4 py-2 rounded-lg bg-accent text-white text-[13px] font-medium hover:bg-accent-hover transition-colors disabled:opacity-50 flex items-center gap-1.5 shrink-0">
                        {savingId === item.id ? <Loader2 size={13} className="animate-spin" /> : null}
                        保存并验证
                      </button>
                    </div>
                    {saveMsg[item.id] && (
                      <div className={`text-[12px] ${saveMsg[item.id].includes('失败') ? 'text-error' : 'text-success'}`}>
                        {saveMsg[item.id]}
                      </div>
                    )}
                  </div>
                ))
              )}
            </div>

            {/* ── OCR / ASR 引擎 Key（DashScope）── */}
            <div className="space-y-2">
              <div className="flex items-center gap-2 text-[13px] font-medium text-dimmed">
                <KeyRound size={14} />OCR / 语音识别引擎（DashScope）
              </div>
              {missingEngine.includes('dashscope') ? (
                <div className="p-4 rounded-xl border border-border-light bg-surface space-y-2">
                  <div className="flex items-center gap-2">
                    <span className="text-[14px] font-medium">阿里云 DashScope</span>
                    <span className="text-[11px] px-1.5 py-0.5 rounded-full bg-warning/10 text-warning font-medium">未配置</span>
                  </div>
                  <p className="text-[12px] text-dimmed">用于扫描件 OCR、图片文字提取与视频语音转写</p>
                  <div className="flex gap-2">
                    <input
                      type="password"
                      value={dsEngineKey}
                      onChange={e => onDsKeyChange(e.target.value)}
                      placeholder="输入 DashScope API Key"
                      className="flex-1 px-3 py-2 rounded-lg border border-border-light bg-surface-hover text-[13px] focus:outline-none focus:border-accent"
                    />
                    <button
                      onClick={onSaveDs}
                      disabled={savingId === 'dashscope-engine'}
                      className="px-4 py-2 rounded-lg bg-accent text-white text-[13px] font-medium hover:bg-accent-hover transition-colors disabled:opacity-50 flex items-center gap-1.5 shrink-0">
                      {savingId === 'dashscope-engine' ? <Loader2 size={13} className="animate-spin" /> : null}
                      保存
                    </button>
                  </div>
                  {saveMsg['dashscope-engine'] && (
                    <div className={`text-[12px] ${saveMsg['dashscope-engine'].includes('失败') ? 'text-error' : 'text-success'}`}>
                      {saveMsg['dashscope-engine']}
                    </div>
                  )}
                </div>
              ) : (
                <div className="flex items-center gap-2 p-3 rounded-xl border border-success/20 bg-success/5 text-success text-[13px]">
                  <ShieldCheck size={16} />DashScope 已配置
                </div>
              )}
            </div>
          </>
        )}

        {/* 底部 */}
        <div className="flex gap-2">
          <button onClick={onBack}
            className="flex-1 py-2.5 rounded-xl border border-border-light text-[14px] hover:bg-surface-hover transition-colors flex items-center justify-center gap-1">
            <ChevronLeft size={16} />
            上一步
          </button>
          <button onClick={onDone}
            className="flex-[2] py-2.5 rounded-xl bg-accent text-white text-[14px] font-medium hover:bg-accent-hover transition-colors flex items-center justify-center gap-2">
            跳过，开始使用
            <ChevronRight size={16} />
          </button>
        </div>
      </div>
    </div>
  )
}
