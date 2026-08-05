import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'

/**
 * 将 LaTeX 分隔符转换为 remark-math 可识别的格式。
 *
 * remark-math 只识别 $ (inline) 和 $$ (display)，但学术论文常使用
 * \(...\) (inline) 和 \[...\] (display)。此函数预处理 Markdown 文本，
 * 将后者转换为前者，使公式能在 KaTeX 中正确渲染。
 *
 * 保护 fenced code blocks (```) 和 inline code (`) 内的内容不被修改。
 */
export function normalizeLatexDelimiters(md: string): string {
  if (!md || typeof md !== 'string') return md
  // 分割出代码块和普通文本，仅处理普通文本
  const parts = md.split(/(```[\s\S]*?```|`[^`\n]+`)/g)
  return parts.map((part, i) => {
    // 奇数索引 = 代码块内容，跳过
    if (i % 2 === 1) return part

    // \[...\] → $$...$$ (display math, 可能跨行)
    part = part.replace(/\\\[([\s\S]*?)\\\]/g, (_: string, latex: string) => {
      // 移除首尾多余空白，保留公式内部结构
      const trimmed = latex.trim()
      // 单行短公式 → 保持紧凑；多行或长公式 → 换行
      if (trimmed.includes('\n') || trimmed.length > 60) {
        return `$$\n${trimmed}\n$$`
      }
      return `$$${trimmed}$$`
    })

    // \(...\) → $...$ (inline math)
    part = part.replace(/\\\(([\s\S]*?)\\\)/g, (_: string, latex: string) => {
      return `$${latex.trim()}$`
    })

    // 含 \tag 的内联公式 → 提升为 display（KaTeX 的 \tag 仅 display 支持，
    // inline 渲染会报 "\tag works only in display equations" 显示红色 katex-error）
    part = part.replace(/\$([^$\n]+?\\tag\s*\{[^}]*\}[^$\n]*?)\$/g, (_: string, latex: string) => {
      return `$$\n${latex.trim()}\n$$`
    })

    return part
  }).join('')
}

/** 全文插件（含 KaTeX 数学公式） — 用于视频/文档等可能含公式的内容 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
// remarkMath 必须在 remarkGfm 之前：公式内的 _（下标）/<（x_{<k}）等字符
// 需先被 math 插件识别为公式语法，否则 GFM 会将 _ 转为斜体、< 解析为 HTML 标签
export const REMARK_PLUGINS: any[] = [remarkMath, remarkGfm]
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const REHYPE_PLUGINS: any[] = [[rehypeKatex, {
  strict: false,
  throwOnError: false,
  errorColor: 'var(--color-text)',
}]]

/** 纯文本插件（无 KaTeX） — 用于网页、纯文本等无公式内容
 *
 *  网页内容常混入 base64/CSS 残留，KaTeX 会误识别为公式并刷屏报错。
 *  此类内容不需要公式渲染，直接跳过 remarkMath + rehypeKatex。
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const REMARK_PLUGINS_PLAIN: any[] = [remarkGfm]
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const REHYPE_PLUGINS_PLAIN: any[] = []
