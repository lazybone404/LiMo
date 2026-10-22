import { apiUrl } from '../api/core'

/**
 * 文档资产（目前只有「原图」`original.png`）的地址。
 *
 * ⚠️ **演示站必须走另一条路**：图片是浏览器直接加载的，**不经过 `window.fetch`**
 * ——`src/demo/mock.ts` 那套假后端对它完全无效。而 `apiUrl('/assets/…')` 在浏览器下
 * 返回站点根下的 `/api/assets/…`，GitHub Pages 上那是 `github.io/api/…`（不是本站），
 * 于是每张图都是一条 404 红字。
 *
 * 所以演示站给一张内联示意图：零网络请求、零静态资源、不会随 base 路径漂移。
 * `VITE_DEMO` 是**编译期常量**，正式构建时整个分支被摇掉，这个字符串也不会留下。
 */
const DEMO_IMAGE =
  'data:image/svg+xml;charset=utf-8,' +
  encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675" viewBox="0 0 1200 675">
  <rect width="1200" height="675" fill="#eceae6"/>
  <rect x="60" y="48" width="1080" height="579" rx="14" fill="#fbfaf8" stroke="#d8d4cc" stroke-width="3"/>
  <rect x="60" y="48" width="1080" height="52" rx="14" fill="#f1efea"/>
  <circle cx="96" cy="74" r="7" fill="#d8d4cc"/>
  <circle cx="120" cy="74" r="7" fill="#d8d4cc"/>
  <circle cx="144" cy="74" r="7" fill="#d8d4cc"/>
  <rect x="112" y="146" width="300" height="20" rx="6" fill="#8d8a83"/>
  <rect x="112" y="196" width="880" height="12" rx="6" fill="#dedad2"/>
  <rect x="112" y="222" width="820" height="12" rx="6" fill="#dedad2"/>
  <rect x="112" y="286" width="250" height="150" rx="10" fill="#e6effa" stroke="#c3d8f2" stroke-width="2"/>
  <rect x="392" y="286" width="250" height="150" rx="10" fill="#f7efe3" stroke="#e8d6bb" stroke-width="2"/>
  <rect x="672" y="286" width="250" height="150" rx="10" fill="#e9f2ea" stroke="#c9e0cd" stroke-width="2"/>
  <rect x="112" y="470" width="880" height="12" rx="6" fill="#dedad2"/>
  <rect x="112" y="496" width="700" height="12" rx="6" fill="#dedad2"/>
  <rect x="112" y="522" width="800" height="12" rx="6" fill="#dedad2"/>
  <text x="600" y="600" font-family="-apple-system, Segoe UI, Microsoft YaHei, sans-serif"
        font-size="20" fill="#a6a29a" text-anchor="middle">静态演示站 · 这里没有真实原图</text>
</svg>`,
  )

export function docAssetUrl(docId: string, name: string): string {
  if (import.meta.env.VITE_DEMO === '1') return DEMO_IMAGE
  return apiUrl(`/assets/${docId}/${name}`)
}
