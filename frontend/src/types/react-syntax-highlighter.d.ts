/**
 * 替代废弃的 @types/react-syntax-highlighter（v15，与 v16 不匹配）
 * react-syntax-highlighter v16 未内置类型声明，此处提供最小覆盖
 */
declare module 'react-syntax-highlighter' {
  import type { ComponentProps, FC } from 'react'
  export const Prism: FC<{
    style?: Record<string, any>
    language?: string
    children?: string
    PreTag?: string | FC<any>
    CodeTag?: string | FC<any>
    showLineNumbers?: boolean
    wrapLines?: boolean
    [key: string]: any
  }>
  export const Light: typeof Prism
}

declare module 'react-syntax-highlighter/dist/esm/styles/prism' {
  export const oneDark: Record<string, any>
  export const oneLight: Record<string, any>
}
