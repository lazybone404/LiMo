import { useMemo, useState, useCallback } from 'react'
import { Plus, Link, FileText, Image, Upload, Loader2, X, Film, Paperclip, KeyRound } from 'lucide-react'
import { EngineSelect, type EngineOption } from './EngineSelect'
import { getSubScenario, DROP_ACCEPT, INPUT_TYPE_LABELS, IMPORTING_LABELS } from './constants'
import { useStore } from '../../store'
import { formatSize } from '../../lib/format'

const MEDIA_INPUT_TYPES = ['video', 'image', 'file']

function validateDropFile(file: File, inputType: string): boolean {
  const exts = DROP_ACCEPT[inputType]
  if (!exts) return true // link/text 不应该有拖拽
  const name = file.name.toLowerCase()
  return exts.some(ext => name.endsWith(ext))
}

interface Props {
  inputType: string; setInputType: (t: 'link' | 'video' | 'text' | 'file' | 'image') => void
  fileObj: File | null; setFileObj: (f: File | null) => void
  fileSubType: string; setFileSubType: (t: string) => void
  importInput: string; setImportInput: (v: string) => void
  importing: boolean; importError: string; importProgress: string
  importPercent: number | null
  engineOptions: Record<string, EngineOption[]>
  engineBindings: Record<string, number | null>
  handleEngineChange: (category: string, modelId: number | null) => void
  handleImport: () => void
  isImageType: boolean
  preOcr: boolean; setPreOcr: (v: boolean) => void
}

export function ImportSection(props: Props) {
  const p = props
  const isDisabled = p.importing || ((p.inputType === 'link' || p.inputType === 'text') ? !p.importInput.trim() : (!p.fileObj && !p.importInput.trim()))

  // ── 拖拽上传 ──
  const [dragOver, setDragOver] = useState(false)
  const addToast = useStore(s => s.addToast)
  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault(); e.stopPropagation(); setDragOver(true)
  }, [])
  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault(); e.stopPropagation()
    // 只在真正离开拖拽区时取消高亮（子元素触发的不算）
    if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragOver(false)
  }, [])
  // P2-6：文件大小预检——后端上传硬上限 2GB；接近上限（>1GB）警告但不拦截。
  // 拖拽 / 文件选择 / 视频选择统一走这里，避免各自为政。
  const MAX_FILE_BYTES = 2 * 1024 * 1024 * 1024
  const WARN_FILE_BYTES = 1 * 1024 * 1024 * 1024
  const pickFile = useCallback((f: File) => {
    if (f.size > MAX_FILE_BYTES) {
      addToast('文件超过 2GB 上传上限，请压缩后重试', 'error')
      return
    }
    if (f.size > WARN_FILE_BYTES) {
      addToast(`文件较大（${formatSize(f.size)}），上传与解析可能较慢`, 'info')
    }
    p.setFileObj(f); p.setImportInput('')
  }, [addToast, p.setFileObj, p.setImportInput])
  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault(); e.stopPropagation(); setDragOver(false)
    const f = e.dataTransfer.files?.[0]
    if (f && validateDropFile(f, p.inputType)) {
      pickFile(f)
    }
  }, [p.inputType, pickFile])
  const dropHandlers = { onDragOver: handleDragOver, onDragLeave: handleDragLeave, onDrop: handleDrop }

  const dropZoneClass = (base: string) =>
    `${base} ${dragOver ? '!border-accent !bg-accent/10' : ''}`

  // 动态文件子类型：从 capabilities 获取
  const caps = useStore(s => s.capabilities)
  const fileTypes = useMemo(() => {
    return Object.keys(caps?.fileTypeMap || {})
  }, [caps])

  // ── P2.2：DashScope 缺 key 提示（图片 OCR / 视频识别需要，前端主动前置）──
  // 内联提示条（非 toast——切类型时不会重复打扰），可 X 关闭（localStorage 记忆）
  const [hintDismissed, setHintDismissed] = useState(() => {
    try { return localStorage.getItem('mbw_dashscope_hint_dismissed') === '1' } catch { return false }
  })
  const needsDashScope = (caps?.missingKeys?.engine?.includes('dashscope') ?? false)
  const showDashScopeHint = needsDashScope && !hintDismissed && (p.inputType === 'video' || p.isImageType)

  // 导入区始终基于用户选择的子标签过滤引擎，不受已选中文档影响
  const filteredDocEngines = useMemo(() => {
    const all = p.engineOptions.doc || []
    const names = caps?.fileTypeMap?.[p.fileSubType] || []
    return all.filter(e => names.includes(e.name))
  }, [p.engineOptions.doc, p.fileSubType, caps])

  return <>
    {/* ── 类型选择器 ── */}
    <div className="flex bg-surface rounded-[10px] p-0.5">
      {(['video', 'file', 'link', 'text', 'image'] as const).map(t => (
        <button key={t} onClick={() => { p.setInputType(t); p.setImportInput(''); p.setFileObj(null) }}
          className={`flex-1 flex items-center justify-center gap-1 py-1.5 rounded-[8px] text-[11px] font-medium transition-all
            ${p.inputType === t ? 'bg-elevated text-foreground shadow-[0_1px_3px_rgba(0,0,0,.08)]' : 'text-muted hover:text-dimmed'}`}>
          {t === 'link' && <Link size={12} />}
          {t === 'video' && <Film size={12} />}
          {t === 'text' && <FileText size={12} />}
          {t === 'file' && <Upload size={12} />}
          {t === 'image' && <Image size={12} />}
          {INPUT_TYPE_LABELS[t]}
        </button>
      ))}
    </div>

    {/* ── 输入区 ── */}
    <div className="rounded-[10px] bg-surface p-3 space-y-2">
      {/* 转录方案 — 视频/文件/图片专属 */}
      {(p.inputType === 'video' || p.inputType === 'file' || p.isImageType) && (
        <>
          {/* P2.2：DashScope 缺 key 前置提示（图片 OCR / 视频识别需要） */}
          {showDashScopeHint && (
            <div className="flex items-start gap-2 p-2.5 rounded-lg bg-warning/10 text-warning text-[11px] leading-relaxed">
              <KeyRound size={13} className="mt-0.5 shrink-0" />
              <span className="flex-1">图片 OCR 与视频语音识别需要 DashScope API Key（设置 → 引擎 → 阿里云 DashScope 填入 Key）</span>
              <button
                onClick={() => { setHintDismissed(true); try { localStorage.setItem('mbw_dashscope_hint_dismissed', '1') } catch {} }}
                className="shrink-0 text-warning/70 hover:text-warning transition-colors" aria-label="关闭提示">
                <X size={12} />
              </button>
            </div>
          )}

          {/* 视频 ASR */}
          {p.inputType === 'video' && p.engineOptions.asr?.length > 0 && (
            <EngineSelect category="asr" label="语音转写" options={p.engineOptions.asr}
              binding={p.engineBindings.asr ?? null} onChange={p.handleEngineChange} />
          )}

          {/* 文件 → 子标签 + Doc 引擎 */}
          {p.inputType === 'file' && (
            <div className="space-y-1">
              {/* 子类型标签 */}
              <div className="flex bg-surface-hover rounded-[7px] p-0.5">
                {fileTypes.map(st => (
                  <button key={st} onClick={() => p.setFileSubType(st)}
                    className={`flex-1 py-1 rounded-[5px] text-[10px] font-medium transition-all
                      ${p.fileSubType === st ? 'bg-elevated text-foreground shadow-[0_1px_2px_rgba(0,0,0,.06)]' : 'text-muted hover:text-dimmed'}`}>
                    {st.toUpperCase()}
                  </button>
                ))}
              </div>
              {filteredDocEngines.length > 0 && (() => {
                const cat = getSubScenario(p.fileSubType)
                return (
                  <EngineSelect category={cat} label="转录方案" options={filteredDocEngines}
                    binding={p.engineBindings[cat] ?? null} onChange={p.handleEngineChange}
                    extraHint={(() => {
                      const names = caps?.fileTypeMap?.[p.fileSubType] || []
                      const first = names[0]
                      const desc = first ? (caps?.engineOptions?.doc || []).find(e => e.name === first)?.desc : ''
                      return desc || `为 ${p.fileSubType.toUpperCase()} 选择合适的转录引擎`
                    })()} />
                )
              })()}
            </div>
          )}

          {/* 图片 OCR */}
          {p.isImageType && p.engineOptions.ocr?.length > 0 && (
            <>
              <EngineSelect category="ocr" label="OCR 引擎" options={p.engineOptions.ocr}
                binding={p.engineBindings.ocr ?? null} onChange={p.handleEngineChange} />
              <label className="flex items-center gap-1.5 cursor-pointer text-[11px] text-muted">
                <input type="checkbox" checked={p.preOcr} onChange={e => p.setPreOcr(e.target.checked)}
                  className="w-3.5 h-3.5 rounded" />
                导入时提取文字
              </label>
            </>
          )}
        </>
      )}

      {/* 网页 / 文本 — 纯输入框 */}
      {(p.inputType === 'link' || p.inputType === 'text') && (
        <>
          <div className="flex items-center gap-1 text-[11px] text-disabled">
            <Plus size={12} />
            <span>{p.inputType === 'link' ? '粘贴网页链接' : '粘贴或输入文本'}</span>
          </div>
          <input value={p.importInput} onChange={e => p.setImportInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && p.handleImport()}
            placeholder={p.inputType === 'link' ? 'https://example.com/article/...' : '粘贴聊天记录、文章、笔记...'}
            disabled={false}
            className="w-full px-2.5 py-2 bg-elevated border border-border-default rounded-lg text-[13px] font-mono outline-none placeholder:text-disabled disabled:opacity-50" />
          {/* 文本类型：支持上传 MD/TXT 文件 */}
          {p.inputType === 'text' && (
            <label className="flex items-center justify-center gap-1.5 py-2 px-3 border border-dashed border-border-default rounded-lg cursor-pointer
              hover:border-accent hover:bg-accent/10 transition-colors text-[11px] text-muted">
              <Upload size={12} />
              <span>或上传 MD · TXT 文件</span>
              <input type="file" className="hidden" accept=".md,.txt,.markdown,.rst"
                onChange={e => {
                  const f = e.target.files?.[0]
                  if (f) {
                    f.text().then(text => { p.setImportInput(text); p.setFileObj(null) }) // M7: 文本内容已提取到 input，不需保留 fileObj
                  }
                }} />
            </label>
          )}
        </>
      )}

      {/* 视频 — URL + 本地文件 */}
      {p.inputType === 'video' && (
        <>
          {!p.fileObj && (
            <>
              <div className="flex items-center gap-1 text-[11px] text-disabled">
                <Plus size={12} /><span>粘贴视频链接</span>
              </div>
              <input value={p.importInput} onChange={e => p.setImportInput(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && p.handleImport()}
                placeholder="https://www.bilibili.com/video/... 或 YouTube 链接"
                disabled={false}
                className="w-full px-2.5 py-2 bg-elevated border border-border-default rounded-lg text-[13px] font-mono outline-none placeholder:text-disabled disabled:opacity-50" />
              <div className="flex items-center gap-2">
                <div className="flex-1 h-px bg-border-default" />
                <span className="text-[10px] text-disabled">或上传本地视频</span>
                <div className="flex-1 h-px bg-border-default" />
              </div>
            </>
          )}
          <label {...dropHandlers}
            className={dropZoneClass("flex flex-col items-center gap-2 py-4 px-3 border-2 border-dashed border-border-default rounded-[10px] cursor-pointer hover:border-accent hover:bg-accent/10 transition-colors text-center")}>
            {p.fileObj ? (
              <>
                <div className="w-9 h-9 rounded-lg bg-accent/10 flex items-center justify-center"><Film size={18} className="text-dimmed" /></div>
                <div>
                  <div className="text-[12px] font-medium text-foreground truncate max-w-full">{p.fileObj.name}</div>
                  <div className="text-[10px] text-disabled">{formatSize(p.fileObj.size)} · 点击更换</div>
                </div>
              </>
            ) : (
              <>
                <Upload size={20} className="text-disabled" />
                <div>
                  <div className="text-[12px] text-dimmed">拖拽或点击选择视频文件</div>
                  <div className="text-[10px] text-disabled mt-0.5">MP4 · MKV · AVI · MOV · WebM</div>
                </div>
              </>
            )}
            <input type="file" className="hidden"
              accept=".mp4,.mkv,.avi,.mov,.webm,.flv,.wmv"
              onChange={e => { const f = e.target.files?.[0]; if (f) pickFile(f) }} />
          </label>
          {p.fileObj && (
            <button onClick={() => p.setFileObj(null)}
              className="w-full py-1 text-[11px] text-muted hover:text-error transition-colors">移除文件</button>
          )}
        </>
      )}

      {/* 文件 / 图片 — 拖拽区 + URL */}
      {(p.inputType === 'file' || p.inputType === 'image') && (
        <>
          <label {...dropHandlers}
            className={dropZoneClass("flex flex-col items-center gap-2 py-5 px-3 border-2 border-dashed border-border-default rounded-[10px] cursor-pointer hover:border-accent hover:bg-accent/10 transition-colors text-center")}>
            {p.fileObj ? (
              <>
                <div className={`w-9 h-9 rounded-lg flex items-center justify-center text-lg ${p.inputType === 'image' ? 'bg-success/10' : 'bg-accent/10'}`}>
                  {p.inputType === 'image' ? <Image size={18} className="text-dimmed" /> : <Paperclip size={18} className="text-dimmed" />}
                </div>
                <div>
                  <div className="text-[12px] font-medium text-foreground truncate max-w-full">{p.fileObj.name}</div>
                  <div className="text-[10px] text-disabled">{formatSize(p.fileObj.size)} · 点击更换</div>
                </div>
              </>
            ) : (
              <>
                <Upload size={20} className="text-disabled" />
                <div>
                  <div className="text-[12px] text-dimmed">{p.inputType === 'image' ? '拖拽图片到此处或点击选择' : '拖拽文件到此处或点击选择'}</div>
                  <div className="text-[10px] text-disabled mt-0.5">
                    {p.inputType === 'image' ? 'PNG · JPG · WebP · BMP · TIFF' : 'PDF · Word · PPT · EPUB · Markdown'}
                  </div>
                </div>
              </>
            )}
            <input type="file" className="hidden"
              accept={p.inputType === 'image' ? '.png,.jpg,.jpeg,.webp,.bmp,.tiff,.gif' : '.pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx,.epub'}
              onChange={e => { const f = e.target.files?.[0]; if (f) pickFile(f) }} />
          </label>
          {p.fileObj && (
            <button onClick={() => p.setFileObj(null)}
              className="w-full py-1 text-[11px] text-muted hover:text-error transition-colors">移除文件</button>
          )}

          {/* 分割线 + URL 输入 */}
          {!p.fileObj && (
            <>
              <div className="flex items-center gap-2">
                <div className="flex-1 h-px bg-border-default" /><span className="text-[10px] text-disabled">或</span><div className="flex-1 h-px bg-border-default" />
              </div>
              <input value={p.importInput} onChange={e => p.setImportInput(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && p.handleImport()}
                placeholder={p.inputType === 'image' ? '或粘贴图片 URL...' : '或粘贴文件 URL...'}
                disabled={false}
                className="w-full px-2.5 py-2 bg-elevated border border-border-default rounded-lg text-[12px] outline-none placeholder:text-disabled disabled:opacity-50" />
            </>
          )}
        </>
      )}

      {/* 进度 & 错误 */}
      {p.importProgress && (
        <div className="space-y-1.5">
          {p.importPercent != null && (
            <div className="h-1 rounded-full bg-surface-hover overflow-hidden">
              <div className="h-full rounded-full bg-accent transition-all duration-500" style={{ width: `${Math.round(p.importPercent)}%` }} />
            </div>
          )}
          <div className="flex items-center gap-2 text-[11px] text-accent">
            <Loader2 size={11} className="animate-spin" />
            {p.importProgress}
            {p.importPercent != null && <span className="text-disabled ml-auto">{Math.round(p.importPercent)}%</span>}
          </div>
        </div>
      )}
      {p.importError && (
        <div className="text-[11px] text-error flex items-center gap-1">
          <X size={11} />{p.importError}
        </div>
      )}

      {/* 导入按钮 */}
      <button onClick={p.handleImport} disabled={isDisabled}
        className="w-full py-1.5 bg-accent text-on-accent text-[12px] font-medium rounded-full hover:bg-accent-hover disabled:opacity-40 transition-all">
        {p.importing
          ? (IMPORTING_LABELS[p.inputType] || '导入中...')
          : MEDIA_INPUT_TYPES.includes(p.inputType) && p.fileObj ? '导入文件'
          : MEDIA_INPUT_TYPES.includes(p.inputType) && p.importInput.trim() ? '导入 URL'
          : '导入'}
      </button>
    </div>
  </>
}
