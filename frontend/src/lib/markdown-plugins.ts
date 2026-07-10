import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'

/** 全局共享的 remark 插件列表，避免每个组件内重复创建新数组 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const REMARK_PLUGINS: any[] = [remarkGfm, remarkMath]

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const REHYPE_PLUGINS: any[] = [[rehypeKatex, { strict: false }]]
