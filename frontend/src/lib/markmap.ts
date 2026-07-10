import { Transformer } from 'markmap-lib'

export const transformer = new Transformer()
// KaTeX CSS 已在 PreviewArea.tsx 中 import，hljs 由 react-syntax-highlighter 内联
// 不调用 markmap-common 的 loadCSS/loadJS，避免 CDN 被 WebView2 拦截
