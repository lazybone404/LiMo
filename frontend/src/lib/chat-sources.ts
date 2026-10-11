/**
 * LiMo — 对话工具的**公网来源**（`search_web` / `fetch_url` 的产出）。
 *
 * ⚠️ **与 `DocRef` 是两件事，不要合并**：`DocRef` 是**库内文档**（`docId` + `title`，
 * 点了跳预览区、不离开应用），这里是**公网来源**（`url` + `title`，点了开浏览器）。
 * 形状不同、跳转语义不同、消费方也不同（前者进引用 chips，后者进工具气泡的展开列表）。
 *
 * ⚠️ **「C 端不暴露完整工具结果」这条约束不管来源列表**：那条管的是 `fetch_url` 的
 * 4000 字正文、`lookup_doc` 的 2000 字全文。来源的 `title` + `url` 与 `refs` 里已有的
 * `{"docId", "title"}` 是同一种粒度 —— 这里的 `title` 还额外截到 200 字（后端做）。
 */

export interface ChatSource {
  title: string
  url: string
}

/**
 * URL 长度上限 —— **与后端 `llm/agent.py::_safe_http_url` 同值**（那边也是 2000）。
 * 跨语言没法共享常量，改一侧时记得看另一侧（两侧都有注释指路）。
 */
const MAX_SOURCE_URL_CHARS = 2000

/**
 * 只放行 http/https 绝对 URL。
 *
 * ⚠️ **这是纵深防御的第二道**，主防线在后端 `llm/agent.py::_safe_http_url`
 * （不合规的 URL 在抽取阶段就被丢掉，根本不会出现在 SSE 事件里）。这里再判一次，
 * 是因为渲染点是 `<a href>` —— 而「后端已经过滤过了」是**关于另一个进程的假设**，
 * 假设在 URL 来源（公网搜索 API）面前不该被信任两次。
 *
 * 判据与后端**同条件**（scheme ∈ {http, https} + 有 host + 无空白 + 长度 ≤ 2000）：
 * 后端用 `urlparse` 比 scheme/netloc，这里用正则比前缀。两者对 `HTTP://X`（大小写）
 * 都放行、对 `javascript:` / `data:` / 裸路径 / 夹带空白的串都拒绝。
 */
export function isSafeSourceUrl(url: unknown): url is string {
  if (typeof url !== 'string') return false
  const u = url.trim()
  // 长度上限：正常搜索返回里不该出现几 KB 的 URL，超长一律当畸形数据
  if (!u || u.length > MAX_SOURCE_URL_CHARS) return false
  // 末尾锚 `$` + `[^\s]+`：既要求 `//` 之后有内容（`https://` 裸协议头不算），
  // 也顺带拒掉夹带空白/换行的畸形串（那种串进 `href` 有可能被浏览器截断解析）。
  return /^https?:\/\/[^\s]+$/i.test(u)
}

/**
 * 过滤出可渲染的来源列表（丢字段 + 丢不合规 URL + 保序去重）。
 *
 * ⚠️ 去重按 **URL**（同一篇文章被两轮搜索命中是常态）；`title` 可能为空串
 * （部分搜索源不返回标题），此时**保留该条**、由渲染层退回显示域名 ——
 * 丢掉它等于把「有来源」变成「没来源」，比显示一个域名差。
 */
export function toChatSources(list: unknown): ChatSource[] {
  if (!Array.isArray(list)) return []
  const seen = new Set<string>()
  const out: ChatSource[] = []
  for (const raw of list) {
    if (!raw || typeof raw !== 'object') continue
    const url = (raw as { url?: unknown }).url
    if (!isSafeSourceUrl(url)) continue
    const key = url.trim()
    if (seen.has(key)) continue
    seen.add(key)
    const title = (raw as { title?: unknown }).title
    out.push({ title: typeof title === 'string' ? title : '', url: key })
  }
  return out
}

/** 取 URL 的域名（`www.` 去掉），用于标题为空时兜底显示 */
export function sourceHost(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}
