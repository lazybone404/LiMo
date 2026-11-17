/**
 * 外链统一走系统浏览器 —— **全仓唯一入口**。
 *
 * 为什么要这个文件：桌面壳是一个 WebView，不是浏览器。正文与对话里的来源链接
 * 都写着 `target="_blank"`，而 WebView 里没有「新标签页」这个东西 —— 点下去要么
 * 在当前这一页导航过去（于是 LiMo 的界面被那个网页整个顶掉，回退键也没有），
 * 要么弹出一个小窗再报错。两种表现都不是用户要的，且都不会留在应用里。
 *
 * 修法是**一个 document 级监听**，不是逐个 `<a>` 加 onClick：
 *
 *  · 链接散在至少六处（正文 Markdown、对话来源气泡、追问 chip、内容横幅、
 *    更新卡片、引导向导），逐个改等于「以后新增一处就得想起这里一次」；
 *  · `target="_blank"` 属性能表达的东西在 WebView 里不成立，留着它当兜底即可。
 *
 * ⚠️ **判据是 href 的 scheme**，不是「有没有 target=_blank」：站内锚点（`#section`）
 * 与相对路径必须原样交给默认行为，否则目录跳转会被当成外链丢给浏览器。
 * ⚠️ 浏览器里跑（`npm run dev` 直接开 5173）走 `window.open`，这样开发时行为一致；
 * 判据复用 `api/core.ts::isTauriEnv`，不另写一份 `__TAURI_INTERNALS__` 检测。
 */
import { isTauriEnv } from '../api/core'

/** 只接管 http(s)。`mailto:` / `file:` / 相对路径 / 锚点一律不动 */
const EXTERNAL_RE = /^https?:\/\//i

/** 打开一个外链。Tauri 里交给系统默认浏览器，浏览器里开新标签 */
export async function openExternal(url: string): Promise<void> {
  if (!EXTERNAL_RE.test(url)) return
  if (isTauriEnv()) {
    // 动态 import：插件只在桌面壳里用得上，不占首屏包体
    const { open } = await import('@tauri-apps/plugin-shell')
    await open(url)
  } else {
    window.open(url, '_blank', 'noopener,noreferrer')
  }
}

/**
 * 装全局监听，返回卸载函数（`main.tsx` 只装一次；严格模式下重复挂的代价是
 * 同一次点击被处理两遍，故必须可卸载）。
 *
 * 拦不拦由三件事共同决定：点在 `<a href>` 上、href 是 http(s)、且不是**可编辑区域内**
 * 的链接（正文编辑器里贴进来的链接不该在点击时飞出去）。
 */
export function installExternalLinkHandler(): () => void {
  const onClick = (e: MouseEvent) => {
    if (e.defaultPrevented) return
    const target = e.target as Element | null
    const anchor = target?.closest?.('a[href]') as HTMLAnchorElement | null
    if (!anchor) return

    const href = anchor.getAttribute('href') ?? ''
    if (!EXTERNAL_RE.test(href)) return

    // 编辑态里的点击是「把光标放进去」，不是「打开它」
    if (anchor.closest('[contenteditable="true"], textarea')) return

    e.preventDefault()
    void openExternal(href)
  }

  document.addEventListener('click', onClick, true)
  return () => document.removeEventListener('click', onClick, true)
}
