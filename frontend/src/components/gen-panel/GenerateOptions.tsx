import { useState, type ChangeEvent } from 'react'
import { ChevronDown, Camera, Upload, FileText, X } from 'lucide-react'
import { normalizeStyle, type TypeMeta } from './constants'
import { useStore } from '../../store'
import { saveReference } from '../../api/references'
import { getErrorMessage } from '../../api/core'

export interface EnabledModel {
  id: number; provider_id: string; model_name: string
  capabilities?: string[]
}
export interface Provider {
  id: string; name: string
}

interface Props {
  // 模型
  selectedModelId: number | null; setSelectedModelId: (v: number | null) => void
  enabledModels: EnabledModel[]; providers: Provider[]
  providerName: (pid: string) => string
  // 格式
  formatToc: boolean; setFormatToc: (v: boolean) => void
  formatLink: boolean; setFormatLink: (v: boolean) => void
  formatSummary: boolean; setFormatSummary: (v: boolean) => void
  formatScreenshot: boolean; setFormatScreenshot: (v: boolean) => void
  // PDF 专属格式
  formatFormulas: boolean; setFormatFormulas: (v: boolean) => void
  formatReferences: boolean; setFormatReferences: (v: boolean) => void
  // 文档图片
  formatImages: boolean; setFormatImages: (v: boolean) => void
  imageAnalyze: boolean; setImageAnalyze: (v: boolean) => void
  imageOptimize: boolean; setImageOptimize: (v: boolean) => void
  ocrEnabled: boolean; setOcrEnabled: (v: boolean) => void
  ocrModelId: number | null; setOcrModelId: (v: number | null) => void
  hasImageModel: boolean  // 当前模型是否支持多模态
  // 风格
  style: string; setStyle: (v: string) => void
  styleKeys: string[]
  // 生成产物
  selectedCore: string[]; setSelectedCore: (v: string[]) => void
  // VL 参数
  videoUnderstanding: boolean; setVideoUnderstanding: (v: boolean) => void
  videoInterval: number; setVideoInterval: (v: number) => void
  gridCols: number; setGridCols: (v: number) => void
  gridRows: number; setGridRows: (v: number) => void
  dedupeEnabled: boolean; setDedupeEnabled: (v: boolean) => void
  unitWidth: number; setUnitWidth: (v: number) => void
  unitHeight: number; setUnitHeight: (v: number) => void
  saveQuality: number; setSaveQuality: (v: number) => void
  frameQualityFilter: boolean; setFrameQualityFilter: (v: boolean) => void
  // 用户备注
  userNotes: string; setUserNotes: (v: string) => void
  notesExpanded: boolean; setNotesExpanded: (v: boolean) => void
  // 参考文件（内容已暂存后端，前端只持 id + 元信息；仅会话内）
  refFile: { id: string; name: string; size: number } | null
  setRefFile: (v: { id: string; name: string; size: number } | null) => void
  // 类型元数据（替代原来 7 个散落的布尔值）
  typeMeta: TypeMeta
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`
  return `${(bytes / 1024).toFixed(1)}KB`
}

export function GenerateOptions(props: Props) {
  const p = props
  const caps = useStore(s => s.capabilities)
  const addToast = useStore(s => s.addToast)
  const [refExpanded, setRefExpanded] = useState(false)
  const [refLoading, setRefLoading] = useState(false)

  const handleRefFile = async (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    e.target.value = ''  // 允许重复选同一文件
    if (!f) return
    if (f.size > 200 * 1024) {
      addToast('参考文件超过 200KB，请精简后重试', 'error')
      return
    }
    setRefLoading(true)
    try {
      const content = await f.text()
      const { id } = await saveReference(f.name, content)
      p.setRefFile({ id, name: f.name, size: f.size })
      addToast(`已选择参考文件 ${f.name}`, 'success')
    } catch (err) {
      addToast(`参考文件暂存失败: ${getErrorMessage(err)}`, 'error')
    } finally {
      setRefLoading(false)
    }
  }

  const currentModel = p.enabledModels.find(m => m.id === p.selectedModelId)
  const isPolishedOnly = p.selectedCore.includes('polished') && !p.selectedCore.includes('notes')
  const isBoth = p.selectedCore.includes('notes') && p.selectedCore.includes('polished')

  // style 参数 normalize（兼容旧 meeting_minutes key）
  const normalizedStyle = normalizeStyle(p.style)

  return <>
    {/* 模型 */}
    <div>
      <span className="text-[12px] font-medium text-dimmed mb-2 block">模型</span>
      {(() => {
        // 图片类型：仅显示有多模态能力的模型
        // 文档类型：显示全部模型（用户可能想用多模态模型处理图片密集型 PDF）
        const filtered = p.typeMeta.isImage
          ? p.enabledModels.filter(m => m.capabilities?.includes('image'))
          : p.enabledModels
        if (filtered.length === 0 && p.typeMeta.isImage) {
          return <p className="text-[12px] text-warning">请先在设置中启用多模态模型（如 GPT-4o、Qwen-VL 等），并绑定到「图片处理」用途</p>
        }
        if (filtered.length === 0) {
          return <p className="text-[12px] text-dimmed">暂无可用模型，请在设置中启用并绑定到对应用途</p>
        }
        // 文档类型也会列出多模态模型（用「视觉」标记），方便用户选用
        return (
          <select value={p.selectedModelId || ''} onChange={e => p.setSelectedModelId(e.target.value ? Number(e.target.value) : null)}
            className="w-full px-3 py-2 bg-surface border border-border-default rounded-[10px] text-[13px] outline-none">
            <option value="">场景默认</option>
            {filtered.map(m => {
              const isImage = m.capabilities?.includes('image')
              return (
                <option key={m.id} value={m.id}>
                  {p.providerName(m.provider_id)} · {m.model_name}{isImage ? ' (视觉)' : ''}
                </option>
              )
            })}
          </select>
        )
      })()}
      {currentModel?.capabilities && (
        <div className="flex gap-1 mt-1.5">
          {currentModel.capabilities.map(c => (
            <span key={c} className="px-1.5 py-0.5 bg-surface-hover rounded-full text-[10px]">{c === 'text' ? '文本' : c === 'image' ? '图片' : c}</span>
          ))}
        </div>
      )}
    </div>

    {/* 链接/视频：笔记格式（Excel 不适用） */}
    {!p.typeMeta.isExcel && p.typeMeta.isMedia && (
      <div className="pt-3 border-t border-border-light">
        <span className="text-[12px] font-medium text-dimmed mb-2 block">笔记格式</span>
        <div className="space-y-1.5">
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={p.formatToc} onChange={e => p.setFormatToc(e.target.checked)} className="w-4 h-4 accent-accent" />
            <span className="text-[13px]">目录</span></label>
          {p.typeMeta.isVideo && (
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={p.formatLink} onChange={e => p.setFormatLink(e.target.checked)} className="w-4 h-4 accent-accent" />
              <span className="text-[13px]">原片跳转</span></label>
          )}
          {p.typeMeta.isVideo && (
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={p.formatScreenshot} onChange={e => p.setFormatScreenshot(e.target.checked)}
                className="w-4 h-4 accent-accent" />
              <span className="text-[13px]">原片截图</span>
              <span className="text-[10px] text-disabled">AI 在文中标记时间点 → 本地 ffmpeg 抽帧嵌入，不经过大模型</span>
            </label>
          )}
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={p.formatSummary} onChange={e => p.setFormatSummary(e.target.checked)} className="w-4 h-4 accent-accent" />
            <span className="text-[13px]">AI 总结</span></label>
        </div>
      </div>
    )}

    {/* 文本/文件：简化格式（Excel 不适用） */}
    {!p.typeMeta.isExcel && (p.typeMeta.sourceType === 'text' || p.typeMeta.isDoc) && (
      <div className="pt-3 border-t border-border-light">
        <span className="text-[12px] font-medium text-dimmed mb-2 block">笔记格式</span>
        <div className="space-y-1.5">
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={p.formatToc} onChange={e => p.setFormatToc(e.target.checked)} className="w-4 h-4 accent-accent" />
            <span className="text-[13px]">目录</span></label>
          {p.typeMeta.isPDF && <>
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={p.formatFormulas} onChange={e => p.setFormatFormulas(e.target.checked)} className="w-4 h-4 accent-accent" />
              <span className="text-[13px]">公式集</span>
              <span className="text-[10px] text-disabled">LaTeX 独立汇总</span></label>
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={p.formatReferences} onChange={e => p.setFormatReferences(e.target.checked)} className="w-4 h-4 accent-accent" />
              <span className="text-[13px]">参考文献</span>
              <span className="text-[10px] text-disabled">BibTeX + 可读列表</span></label>
          </>}
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={p.formatSummary} onChange={e => p.setFormatSummary(e.target.checked)} className="w-4 h-4 accent-accent" />
            <span className="text-[13px]">AI 总结</span></label>
          {(p.typeMeta.hasImages) && (
            <>
              {/* 三个正交维度，独立组合，互不依赖：
                  保留图片引用 / OCR 提取文字 / AI 分析图片 可任意搭配 */}
              <label className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked={p.formatImages} onChange={e => p.setFormatImages(e.target.checked)}
                  className="w-4 h-4 accent-accent" />
                <span className="text-[13px]">保留文档图片</span>
                <span className="text-[10px] text-disabled">笔记中保留 ![](assets/xxx) 图片引用</span></label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked={p.ocrEnabled} onChange={e => p.setOcrEnabled(e.target.checked)}
                  className="w-4 h-4 accent-accent" />
                <span className="text-[13px]">OCR 提取图中文字</span>
                <span className="text-[10px] text-disabled">先用 OCR 模型识别图片内容插入原文，再交给文本模型生成</span>
              </label>
              {p.ocrEnabled && (
                <div className="ml-10">
                  <span className="text-[11px] text-dimmed mb-1 block">OCR 模型</span>
                  {(() => {
                    const ocrModels = p.enabledModels.filter(m => m.capabilities?.includes('image'))
                    if (ocrModels.length === 0) return <p className="text-[12px] text-warning">暂无可用 OCR 模型，请在设置中启用视觉模型</p>
                    return (
                      <select value={p.ocrModelId || ''} onChange={e => p.setOcrModelId(e.target.value ? Number(e.target.value) : null)}
                        className="w-full px-3 py-1.5 bg-surface border border-border-default rounded-[10px] text-[12px] outline-none">
                        <option value="">选择 OCR 模型</option>
                        {ocrModels.map(m => (
                          <option key={m.id} value={m.id}>{p.providerName(m.provider_id)} · {m.model_name}</option>
                        ))}
                      </select>
                    )
                  })()}
                </div>
              )}
              <label className={`flex items-center gap-2 ${p.hasImageModel ? 'cursor-pointer' : 'cursor-not-allowed'}`}>
                <input type="checkbox" checked={p.imageAnalyze} onChange={e => p.setImageAnalyze(e.target.checked)}
                  disabled={!p.hasImageModel} className="w-4 h-4 accent-accent disabled:opacity-30" />
                <span className={`text-[13px] ${!p.hasImageModel ? 'text-disabled' : ''}`}>AI 分析图片</span>
                <span className="text-[10px] text-disabled">
                  {p.hasImageModel ? '图片发给多模态 LLM 做视觉理解' : '需多模态模型（当前模型不支持图片）'}
                </span></label>
              {p.imageAnalyze && (
                <label className="flex items-center gap-2 cursor-pointer ml-10">
                  <input type="checkbox" checked={p.imageOptimize} onChange={e => p.setImageOptimize(e.target.checked)} className="w-3.5 h-3.5 accent-accent" />
                  <span className="text-[12px]">图片预处理</span>
                  <span className="text-[10px] text-disabled">PIL 缩放（长边≤1024px），省 Token</span>
                </label>
              )}
            </>
          )}
        </div>
      </div>
    )}

    {/* 生成产物 — 非图片类型，从注册表动态渲染 */}
    {!p.typeMeta.isImage && p.typeMeta.coreProducts.length > 0 && (
      <div className="pt-3 border-t border-border-light">
        <span className="text-[12px] font-medium text-dimmed mb-2 block">生成产物</span>
        <div className="space-y-1.5">
          {p.typeMeta.coreProducts.map(prod => {
            const checked = p.selectedCore.includes(prod.name)
            const toggle = () => {
              if (checked) p.setSelectedCore(p.selectedCore.filter(n => n !== prod.name))
              else p.setSelectedCore([...p.selectedCore, prod.name])
            }
            return (
              <label key={prod.name} className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked={checked} onChange={toggle}
                  className="w-4 h-4 accent-accent" />
                <span className="text-[13px]">{prod.label}</span>
              </label>
            )
          })}
        </div>
      </div>
    )}

    {/* AI 画面理解 — 视频专属 */}
    {p.typeMeta.isVideo && (
      <div className="pt-3 border-t border-border-light">
        <span className="text-[12px] font-medium text-dimmed mb-2 block">AI 画面理解</span>
        <span className="text-[11px] text-disabled block -mt-1 mb-2">截取视频画面发给多模态模型辅助分析</span>
        {!p.hasImageModel && (
          <p className="text-[11px] text-warning mb-2">需多模态模型（当前模型不支持图片），请在设置中启用</p>
        )}
        <label className={`flex items-center gap-2 mb-2 ${p.hasImageModel ? 'cursor-pointer' : 'cursor-not-allowed'}`}>
          <input type="checkbox" checked={p.videoUnderstanding} onChange={e => p.setVideoUnderstanding(e.target.checked)}
            disabled={!p.hasImageModel} className="w-4 h-4 accent-accent disabled:opacity-30" />
          <Camera size={13} className="text-muted" />
          <span className={`text-[13px] ${!p.hasImageModel ? 'text-disabled' : ''}`}>启用</span>
        </label>
        {p.videoUnderstanding && (
          <div className="space-y-2 pl-6 border-l-2 border-accent/20">
            <div>
              <label className="text-[11px] text-muted block mb-0.5">截图间隔（秒）</label>
              <input type="number" value={p.videoInterval} onChange={e => p.setVideoInterval(Math.max(1, Math.min(30, +e.target.value)))}
                className="w-20 px-2 py-1.5 bg-surface border border-border-default rounded-lg text-[12px] outline-none" />
            </div>
            <div>
              <label className="text-[11px] text-muted block mb-0.5">拼图尺寸（列 × 行）</label>
              <div className="flex items-center gap-1">
                <input type="number" value={p.gridCols} onChange={e => p.setGridCols(Math.max(1, Math.min(10, +e.target.value)))}
                  className="w-14 px-2 py-1.5 bg-surface border border-border-default rounded-lg text-[12px] outline-none" />
                <span className="text-[12px] text-disabled">×</span>
                <input type="number" value={p.gridRows} onChange={e => p.setGridRows(Math.max(1, Math.min(10, +e.target.value)))}
                  className="w-14 px-2 py-1.5 bg-surface border border-border-default rounded-lg text-[12px] outline-none" />
              </div>
            </div>
            <details className="text-[11px] text-muted">
              <summary className="cursor-pointer hover:text-dimmed">高级设置</summary>
              <div className="mt-2 space-y-2">
                <div>
                  <label className="text-[11px] text-muted block mb-0.5">单帧分辨率</label>
                  <div className="flex items-center gap-1">
                    <input type="number" value={p.unitWidth} onChange={e => p.setUnitWidth(Math.max(160, Math.min(1920, +e.target.value)))}
                      className="w-14 px-2 py-1.5 bg-surface border border-border-default rounded-lg text-[12px] outline-none" />
                    <span className="text-[12px] text-disabled">×</span>
                    <input type="number" value={p.unitHeight} onChange={e => p.setUnitHeight(Math.max(120, Math.min(1080, +e.target.value)))}
                      className="w-14 px-2 py-1.5 bg-surface border border-border-default rounded-lg text-[12px] outline-none" />
                  </div>
                </div>
                <div>
                  <label className="text-[11px] text-muted block mb-0.5">JPEG 质量</label>
                  <input type="number" value={p.saveQuality} onChange={e => p.setSaveQuality(Math.max(1, Math.min(100, +e.target.value)))}
                    className="w-20 px-2 py-1.5 bg-surface border border-border-default rounded-lg text-[12px] outline-none" />
                </div>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" checked={p.dedupeEnabled} onChange={e => p.setDedupeEnabled(e.target.checked)} className="w-3.5 h-3.5 accent-accent" />
                  <span className="text-[11px]">相邻重复帧去重</span></label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" checked={p.frameQualityFilter} onChange={e => p.setFrameQualityFilter(e.target.checked)} className="w-3.5 h-3.5 accent-accent" />
                  <span className="text-[11px]">帧质量过滤（跳过模糊/纯色帧）</span></label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" checked={p.imageOptimize} onChange={e => p.setImageOptimize(e.target.checked)} className="w-3.5 h-3.5 accent-accent" />
                  <span className="text-[11px]">图片预处理（PIL 缩放 ≤1024px，省 Token）</span></label>
              </div>
            </details>
          </div>
        )}
      </div>
    )}

    {/* 风格 — 仅 notes / both 模式显示；polished only 隐藏 */}
    {!isPolishedOnly && (p.typeMeta.isMedia || p.typeMeta.sourceType === 'text' || p.typeMeta.isDoc) && (
      <div className="pt-3 border-t border-border-light">
        <span className="text-[12px] font-medium text-dimmed mb-2 block">
          {isBoth ? '笔记风格（整理版固定忠实整理）' : '风格'}
        </span>
        <div className="flex gap-1 flex-wrap">
          {p.styleKeys.map(s => (
            <button key={s} onClick={() => p.setStyle(normalizeStyle(s))}
              className={`px-3 py-1.5 text-[13px] rounded-full font-medium transition-colors ${normalizedStyle === normalizeStyle(s) ? 'bg-accent text-white' : 'text-dimmed hover:bg-surface-hover'}`}>
              {caps?.styleLabels?.[s] || s}</button>
          ))}
        </div>
        {isBoth && (
          <p className="text-[11px] text-dimmed mt-1.5">风格选择仅影响「笔记」产物，「整理版」固定为忠实原文整理。</p>
        )}
      </div>
    )}

    {/* 参考文件 — 非图片类型均可（内容暂存后端，仅 notes 注入） */}
    {!p.typeMeta.isImage && <div className="pt-3 border-t border-border-light">
      <button onClick={() => setRefExpanded(!refExpanded)}
        className="flex items-center gap-1.5 text-[12px] font-medium text-dimmed hover:text-foreground transition-colors">
        <ChevronDown size={12} className={`transition-transform ${refExpanded ? '' : '-rotate-90'}`} />
        <span>参考文件</span>
        {p.refFile && <span className="w-1.5 h-1.5 rounded-full bg-accent" />}
      </button>
      {refExpanded && (
        <div className="mt-2 space-y-2">
          <label className={`flex items-center justify-center gap-1.5 py-2 px-3 border border-dashed border-border-default rounded-lg text-[11px] text-muted transition-colors ${isPolishedOnly ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer hover:border-accent hover:bg-accent/10'}`}>
            <Upload size={12} />
            <span>{isPolishedOnly ? '整理版原文不支持参考文件' : (refLoading ? '暂存中...' : '选择参考文件（txt / md，≤200KB）')}</span>
            <input type="file" className="hidden" accept=".txt,.md,.markdown" onChange={handleRefFile} disabled={isPolishedOnly} />
          </label>
          <p className="text-[10px] text-disabled leading-relaxed">
            {isPolishedOnly ? '整理版原文为忠实整理，不注入参考资料。' : '参考资料作为背景注入笔记生成（仅 notes），与原文冲突时以原文/模型判断为准。'}
          </p>
          {p.refFile && (
            <div className="flex items-center gap-2 px-2.5 py-1.5 bg-surface border border-border-default rounded-lg text-[12px]">
              <FileText size={12} className="shrink-0 text-muted" />
              <span className="flex-1 min-w-0 truncate text-dimmed">{p.refFile.name}</span>
              <span className="text-[10px] text-disabled shrink-0">{formatSize(p.refFile.size)}</span>
              <button onClick={() => p.setRefFile(null)} aria-label="移除参考文件"
                className="text-muted hover:text-red-500 shrink-0">
                <X size={12} />
              </button>
            </div>
          )}
        </div>
      )}
    </div>}

    {/* 用户备注 / 分析指引 — 非图片类型均可 */}
    {!p.typeMeta.isImage && <div className="pt-3 border-t border-border-light">
      <button onClick={() => p.setNotesExpanded(!p.notesExpanded)}
        className="flex items-center gap-1.5 text-[12px] font-medium text-dimmed hover:text-foreground transition-colors">
        <ChevronDown size={12} className={`transition-transform ${p.notesExpanded ? '' : '-rotate-90'}`} />
        <span>{p.typeMeta.isExcel ? '分析指引' : '用户备注'}</span>
        {p.userNotes.trim() && <span className="w-1.5 h-1.5 rounded-full bg-accent" />}
      </button>
      {p.notesExpanded && (
        <textarea value={p.userNotes}
          onChange={e => { if (!isPolishedOnly) p.setUserNotes(e.target.value) }}
          disabled={isPolishedOnly}
          placeholder={p.typeMeta.isExcel
            ? '输入你的分析要求：关注哪些指标？需要什么对比维度？有什么假设需要验证？…'
            : isPolishedOnly
              ? '整理版原文不接受备注（仅「笔记」生成时可填）'
              : p.refFile
                ? '说明参考文件来源与用途，如：来自视频评论区，请参考其中的讨论角度'
                : '输入你的备注、上下文或特殊要求，AI 生成时会参考这些信息…'}
          rows={3}
          className="w-full mt-2 px-2.5 py-2 bg-surface border border-border-default rounded-lg text-[12px] outline-none resize-none placeholder:text-disabled disabled:opacity-50" />
      )}
    </div>}
  </>
}
