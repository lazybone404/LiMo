import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'

/** 全局共享的 remark 插件列表，避免每个组件内重复创建新数组 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
// remarkMath 必须在 remarkGfm 之前：公式内的 _（下标）/<（x_{<k}）等字符
// 需先被 math 插件识别为公式语法，否则 GFM 会将 _ 转为斜体、< 解析为 HTML 标签
export const REMARK_PLUGINS: any[] = [remarkMath, remarkGfm]

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const REHYPE_PLUGINS: any[] = [[rehypeKatex, { strict: false }]]
