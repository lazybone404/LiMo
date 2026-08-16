/**
 * TextareaMarkdownEditor — 产物编辑器的第一实现（零依赖：textarea + 选区操作 + 分屏实时预览）。
 *
 * 这是方案文档中 `MarkdownEditor` 接口（{ value, onChange, onSave, onCancel }）的落地实现。
 * 未来可替换为 ByteMdEditor（同源 Unified 插件平移，CM5 内核为已知技术债）——
 * 调用方（PreviewArea）只依赖本组件 props 契约，不依赖内部实现。
 *
 * 交互：工具栏 + 快捷键（Ctrl+B/I/S 行内、Ctrl+Z/Y 撤销重做、Ctrl+S 保存）+ 左源码右预览分屏。
 * 预览复用现有渲染管线（REMARK/REHYPE 插件 + normalizeLatexDelimiters + createMarkdownComponents）。
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { REMARK_PLUGINS, REHYPE_PLUGINS, normalizeLatexDelimiters } from '../../lib/markdown-plugins'
import { createMarkdownComponents } from '../../lib/markdown-components'
import {
  Bold, Italic, Strikethrough, Heading1, Heading2, Heading3,
  List, Quote, Code, Table, Link, Undo2, Redo2, Save, X, Loader2,
} from 'lucide-react'
import { ResizeHandle } from '../ui/ResizeHandle'

export interface MarkdownEditorProps {
  /** 受控 Markdown 源码（PreviewArea 持有 editValue，保证保存/防丢能拿到最新值） */
  value: string
  onChange: (v: string) => void
  onSave: () => void
  onCancel: () => void
  /** 是否有未保存修改（保存按钮禁用条件） */
  dirty: boolean
  /** 保存请求中（禁用保存防重复） */
  saving?: boolean
  sourceUrl?: string
  isVideo?: boolean
}

const HISTORY_CAP = 100

export function TextareaMarkdownEditor({
  value, onChange, onSave, onCancel, dirty, saving, sourceUrl, isVideo,
}: MarkdownEditorProps) {
  const taRef = useRef<HTMLTextAreaElement>(null)
  // 分屏拖拽：源码区宽度百分比（预览占剩余）。clamp [25,75] 防止过度挤压任一侧
  const splitRef = useRef<HTMLDivElement>(null)
  const [splitPct, setSplitPct] = useState(50)
  const resizeSplit = useCallback((dx: number) => {
    const w = splitRef.current?.offsetWidth
    if (!w) return
    setSplitPct(p => Math.min(75, Math.max(25, p + (dx / w) * 100)))
  }, [])
  // 历史栈用 ref 保存：撤销/重做读取即时状态（不依赖 render 闭包，连按 Ctrl+Z 也正确），
  // 且不在 setState updater 内调副作用（React 18+ 不保证 updater 只执行一次）。
  // state 长度仅驱动撤销/重做按钮的 disabled 显示。
  const historyRef = useRef<string[]>([])
  const futureRef = useRef<string[]>([])
  const [historyLen, setHistoryLen] = useState(0)
  const [futureLen, setFutureLen] = useState(0)
  // render 时同步最新值/回调到 ref —— 快捷键与撤销 handler 不依赖陈旧闭包
  const valueRef = useRef(value)
  const onChangeRef = useRef(onChange)
  valueRef.current = value
  onChangeRef.current = onChange

  // 分屏预览 debounce 300ms（防大文档每键重渲染）
  const [previewValue, setPreviewValue] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setPreviewValue(value), 300)
    return () => clearTimeout(t)
  }, [value])

  const components = useMemo(() => createMarkdownComponents(sourceUrl, isVideo), [sourceUrl, isVideo])

  const pushHistory = useCallback((snapshot: string) => {
    historyRef.current = [...historyRef.current.slice(-(HISTORY_CAP - 1)), snapshot]
    futureRef.current = []
    setHistoryLen(historyRef.current.length)
    setFutureLen(0)
  }, [])

  /** 统一提交：记录变更前快照 → 通知父级。ta.value 是变更前值（onChange 尚未触发） */
  const commit = useCallback((next: string, ta: HTMLTextAreaElement) => {
    pushHistory(ta.value)
    onChangeRef.current(next)
  }, [pushHistory])

  const undo = useCallback(() => {
    const h = historyRef.current
    if (h.length === 0) return
    const prev = h[h.length - 1]
    historyRef.current = h.slice(0, -1)
    futureRef.current = [...futureRef.current, valueRef.current]
    setHistoryLen(historyRef.current.length)
    setFutureLen(futureRef.current.length)
    onChangeRef.current(prev)
    valueRef.current = prev  // 同步，防连按撤销时 future 重复记录同一值
  }, [])

  const redo = useCallback(() => {
    const f = futureRef.current
    if (f.length === 0) return
    const next = f[f.length - 1]
    futureRef.current = f.slice(0, -1)
    historyRef.current = [...historyRef.current, valueRef.current]
    setHistoryLen(historyRef.current.length)
    setFutureLen(futureRef.current.length)
    onChangeRef.current(next)
    valueRef.current = next
  }, [])

  /** 行内操作：用标记包裹选中文本（空选区用 placeholder），保持选区选中 */
  const wrapSelection = useCallback((before: string, after: string, placeholder: string) => {
    const ta = taRef.current
    if (!ta) return
    const s = ta.selectionStart, e = ta.selectionEnd
    const cur = valueRef.current
    const selected = cur.slice(s, e) || placeholder
    const next = cur.slice(0, s) + before + selected + after + cur.slice(e)
    commit(next, ta)
    requestAnimationFrame(() => {
      ta.focus()
      ta.setSelectionRange(s + before.length, s + before.length + selected.length)
    })
  }, [commit])

  /** 行级操作：对选区覆盖的每一行套用转换函数（标题/列表/引用），光标定位到块首 */
  const lineOperation = useCallback((fn: (line: string) => string) => {
    const ta = taRef.current
    if (!ta) return
    const s = ta.selectionStart, e = ta.selectionEnd
    const cur = valueRef.current
    const start = cur.lastIndexOf('\n', s - 1) + 1
    const endIdx = cur.indexOf('\n', e)
    const endPos = endIdx === -1 ? cur.length : endIdx
    const block = cur.slice(start, endPos)
    const next = cur.slice(0, start) + block.split('\n').map(fn).join('\n') + cur.slice(endPos)
    commit(next, ta)
    requestAnimationFrame(() => { ta.focus(); ta.setSelectionRange(start, start) })
  }, [commit])

  const toggleHeading = (n: number) => lineOperation(line => {
    const m = line.match(/^(#{1,6})\s/)
    if (m) return `${'#'.repeat(n)} ${line.slice(m[0].length)}`
    return `${'#'.repeat(n)} ${line}`
  })
  const toggleList = () => lineOperation(line => /^[-*]\s/.test(line) ? line.replace(/^[-*]\s/, '') : `- ${line}`)
  const toggleQuote = () => lineOperation(line => line.startsWith('> ') ? line.slice(2) : `> ${line}`)

  /** 代码块：包裹选区（多行），空选区插入模板 */
  const insertCodeBlock = useCallback(() => {
    const ta = taRef.current
    if (!ta) return
    const s = ta.selectionStart, e = ta.selectionEnd
    const cur = valueRef.current
    const selected = cur.slice(s, e) || '代码'
    const block = '```\n' + selected + '\n```'
    const next = cur.slice(0, s) + block + cur.slice(e)
    commit(next, ta)
    requestAnimationFrame(() => { ta.focus(); ta.setSelectionRange(s + 4, s + 4 + selected.length) })
  }, [commit])

  /** 表格：在光标处插入 3 行模板并选中 */
  const insertTable = useCallback(() => {
    const ta = taRef.current
    if (!ta) return
    const s = ta.selectionStart, e = ta.selectionEnd
    const cur = valueRef.current
    const table = '| 列1 | 列2 | 列3 |\n| --- | --- | --- |\n| 数据 | 数据 | 数据 |'
    const next = cur.slice(0, s) + table + cur.slice(e)
    commit(next, ta)
    requestAnimationFrame(() => { ta.focus(); ta.setSelectionRange(s, s + table.length) })
  }, [commit])

  const handleChange = useCallback((ev: React.ChangeEvent<HTMLTextAreaElement>) => {
    pushHistory(valueRef.current)
    onChange(ev.target.value)
  }, [onChange, pushHistory])

  // 快捷键（Ctrl+Z/Y 撤销重做、Ctrl+B/I 行内、Ctrl+S 保存）
  useEffect(() => {
    const onKeyDown = (ev: KeyboardEvent) => {
      if (!ev.ctrlKey && !ev.metaKey) return
      const k = ev.key.toLowerCase()
      if (k === 'z' && !ev.shiftKey) { ev.preventDefault(); undo() }
      else if (k === 'y' || (k === 'z' && ev.shiftKey)) { ev.preventDefault(); redo() }
      else if (k === 'b') { ev.preventDefault(); wrapSelection('**', '**', '加粗文字') }
      else if (k === 'i') { ev.preventDefault(); wrapSelection('*', '*', '斜体文字') }
      else if (k === 's') { ev.preventDefault(); if (dirty) onSave() }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [undo, redo, wrapSelection, dirty, onSave])

  const ToolBtn = ({ title, onClick, disabled, children }: { title: string; onClick: () => void; disabled?: boolean; children: React.ReactNode }) => (
    <button
      title={title}
      onClick={onClick}
      disabled={disabled}
      className="w-7 h-7 flex items-center justify-center rounded-md text-dimmed hover:bg-surface-hover hover:text-foreground transition-colors disabled:opacity-35 disabled:pointer-events-none"
    >{children}</button>
  )

  const Divider = () => <div className="w-px h-4 bg-border-light mx-1 shrink-0" />

  return (
    <div className="flex flex-col h-full min-h-0 border border-border-light rounded-xl overflow-hidden">
      {/* 工具栏 */}
      <div className="flex items-center gap-0.5 px-2.5 py-1.5 border-b border-border-light bg-surface-hover/50 shrink-0 flex-wrap">
        <ToolBtn title="加粗 (Ctrl+B)" onClick={() => wrapSelection('**', '**', '加粗文字')}><Bold size={14} /></ToolBtn>
        <ToolBtn title="斜体 (Ctrl+I)" onClick={() => wrapSelection('*', '*', '斜体文字')}><Italic size={14} /></ToolBtn>
        <ToolBtn title="删除线" onClick={() => wrapSelection('~~', '~~', '删除线文字')}><Strikethrough size={14} /></ToolBtn>
        <Divider />
        <ToolBtn title="标题 1" onClick={() => toggleHeading(1)}><Heading1 size={14} /></ToolBtn>
        <ToolBtn title="标题 2" onClick={() => toggleHeading(2)}><Heading2 size={14} /></ToolBtn>
        <ToolBtn title="标题 3" onClick={() => toggleHeading(3)}><Heading3 size={14} /></ToolBtn>
        <Divider />
        <ToolBtn title="无序列表" onClick={toggleList}><List size={14} /></ToolBtn>
        <ToolBtn title="引用" onClick={toggleQuote}><Quote size={14} /></ToolBtn>
        <ToolBtn title="代码块" onClick={insertCodeBlock}><Code size={14} /></ToolBtn>
        <ToolBtn title="表格" onClick={insertTable}><Table size={14} /></ToolBtn>
        <Divider />
        <ToolBtn title="链接" onClick={() => wrapSelection('[', '](url)', '链接文字')}><Link size={14} /></ToolBtn>
        <Divider />
        <ToolBtn title="撤销 (Ctrl+Z)" onClick={undo} disabled={historyLen === 0}><Undo2 size={14} /></ToolBtn>
        <ToolBtn title="重做 (Ctrl+Y)" onClick={redo} disabled={futureLen === 0}><Redo2 size={14} /></ToolBtn>
        <div className="flex-1" />
        <span className="text-[11px] text-disabled mr-1 hidden lg:inline">Ctrl+B 加粗 · Ctrl+I 斜体 · Ctrl+Z/Y 撤销重做 · Ctrl+S 保存</span>
      </div>

      {/* 分屏：左源码 / 右实时预览，中间分隔条可拖拽调宽（对齐三栏布局交互） */}
      <div ref={splitRef} className="flex flex-1 min-h-0">
        <textarea
          ref={taRef}
          value={value}
          onChange={handleChange}
          spellCheck={false}
          className="min-w-0 h-full resize-none outline-none bg-surface text-foreground font-mono text-[13px] leading-relaxed p-4"
          style={{ flex: `0 0 ${splitPct}%` }}
          placeholder="在此编辑 Markdown 源码..."
        />
        <ResizeHandle direction="horizontal" onResize={resizeSplit} />
        <div className="flex-1 min-w-0 h-full overflow-y-auto bg-surface">
          <div className="markdown-body p-4">
            <ReactMarkdown remarkPlugins={REMARK_PLUGINS} rehypePlugins={REHYPE_PLUGINS} components={components}>
              {normalizeLatexDelimiters(previewValue)}
            </ReactMarkdown>
          </div>
        </div>
      </div>

      {/* 底部操作栏 */}
      <div className="flex items-center justify-end gap-2 px-4 py-2.5 border-t border-border-light shrink-0 bg-surface-hover/30">
        <button
          onClick={onCancel}
          className="px-4 py-1.5 text-[12px] text-dimmed hover:bg-surface-hover rounded-lg transition-colors flex items-center gap-1"
        ><X size={13} /> 取消</button>
        <button
          onClick={onSave}
          disabled={!dirty || saving}
          className="px-4 py-1.5 text-[12px] bg-accent text-white rounded-lg hover:bg-accent-hover disabled:opacity-40 disabled:pointer-events-none transition-colors flex items-center gap-1"
        >{saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />} 保存</button>
      </div>
    </div>
  )
}
