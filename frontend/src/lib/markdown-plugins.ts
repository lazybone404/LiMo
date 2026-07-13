import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'

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
