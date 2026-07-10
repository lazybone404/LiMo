/** 首次启动向导 — 检测可选的系统组件（ffmpeg / SearXNG） */
import { useState, useEffect } from 'react'
import { Check, X, Download, ChevronRight } from 'lucide-react'

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

export function SetupWizard({ onDone }: Props) {
  const [components, setComponents] = useState<ComponentStatus[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    // 仅 Tauri 环境可调用 Rust command
    const isTauri = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
    if (!isTauri) {
      setLoading(false)
      onDone()
      return
    }

    import('@tauri-apps/api/core')
      .then(({ invoke }) => invoke<ComponentStatus[]>('detect_components'))
      .then(setComponents)
      .catch(() => {
        // 调用失败（开发模式或旧版本），跳过向导
        onDone()
      })
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <div className="fixed inset-0 z-[100] flex items-center justify-center bg-[var(--tk-elevated)]">
        <div className="animate-pulse text-dimmed text-[15px]">正在检测系统环境…</div>
      </div>
    )
  }

  if (components.length === 0) {
    onDone()
    return null
  }

  const allFound = components.every(c => c.status !== 'missing')

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 backdrop-blur-sm">
      <div className="bg-elevated rounded-2xl shadow-xl border border-border-light w-[480px] max-h-[90vh] overflow-y-auto p-8 space-y-6">
        {/* 头部 */}
        <div className="text-center space-y-2">
          <h1 className="text-[22px] font-semibold">欢迎使用 LiMo</h1>
          <p className="text-[14px] text-dimmed leading-relaxed">
            首次启动，正在检测可选系统组件。
            <br />
            缺失的组件可在设置中随时补充安装。
          </p>
        </div>

        {/* 组件列表 */}
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

        {/* 底部 */}
        <button onClick={onDone}
          className="w-full py-2.5 rounded-xl bg-accent text-white text-[14px] font-medium hover:bg-accent-hover transition-colors flex items-center justify-center gap-2">
          {allFound ? '开始使用' : '知道了，开始使用'}
          <ChevronRight size={16} />
        </button>
        <p className="text-center text-[11px] text-disabled">
          组件状态可在「设置 → 通用」中重新检测
        </p>
      </div>
    </div>
  )
}
