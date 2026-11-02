/**
 * 从「用户粘贴进来的那段东西」里认出 URL —— 导入框的入口判据。
 *
 * ⚠️ 改造前只判 `text.startsWith('http://')`，于是三种常见输入被当成纯文本导入：
 *   ① B 站 App 的分享文案 `【标题】 https://b23.tv/xxxx`（URL 前面还有字）
 *   ② 从地址栏拷时丢了协议的 `www.bilibili.com/video/BV…`
 *   ③ 同理的 `b23.tv/xxxx`
 * 这三种都会「导入成功」—— 只是导进去的是一段文字，而不是那支视频。产物上看不出异常。
 *
 * **判据必须保守**：漏判的代价是用户手动补个 `https://`；误判的代价是**一篇正文被当成 URL 发出去**，
 * 内容直接没了。所以「夹带 URL 也算 URL」只在「URL 几乎占满整段输入」时才成立。
 */

/**
 * 夹带 URL 时，URL 之外的文字最多允许多少（去空白）。分享文案里通常就是标题。
 *
 * ⚠️ **这是启发式，取值刻意偏严。** 它是用来看「这段输入的主语是不是那个链接」的，
 * 而两种错方向的代价不对称：判小了，用户看到导入进去的是一段文字，删掉前缀重来即可；
 * 判大了，一篇文章被发去当 URL 下载，界面上只显示「下载中…」，正文直接没了。
 * 实测一句话「……顺便贴了个链接 <url> 供参考」（46 字剩余）就落在 40 之外 —— 这正是
 * 要的那一侧。要放宽之前，先想清楚怎么把「用户其实想导入正文」这个情况接住。
 */
const MAX_FRAGMENT_CHARS = 40

/** 带协议的 URL。结尾的空白/引号/中文标点不吞 —— 它们几乎总是包裹符而不是 URL 的一部分。 */
const URL_IN_TEXT = /https?:\/\/[^\s<>"'，。！？、；：）】》」』]+/i

/** 裸域名（无协议）：只认两种形态，见下方说明。 */
const BARE_WITH_PATH = /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+\/\S*$/i
const BARE_WWW = /^www\.\S+$/i

/** 剥掉 URL 尾部粘着的标点（`（见 https://a.com/x）` 这类）。 */
function trimTrailing(url: string): string {
  return url.replace(/[),.;!?。，、；：）】》」』"'…]+$/, '')
}

/**
 * 判断一段导入输入是不是 URL；是则返回**可以直接交给后端**的 URL，否则 `null`（当纯文本）。
 *
 * 返回值和入参可能不同（去掉了分享文案前缀 / 补上了 `https://`）—— 调用方必须用返回值，
 * 而不是原串。
 */
export function detectImportUrl(input: string): string | null {
  const text = input.trim()
  if (!text) return null

  // ① 正文就是 URL（改造前的唯一判据，零回归）。
  if (/^https?:\/\//i.test(text)) return text

  // ② 分享文案：URL 前面还挂着一句标题。**必须单行**且剩余文字很少 ——
  //    一段多行笔记里引用一个链接是多行文本的常态，不是一次 URL 导入。
  if (!/\n/.test(text)) {
    const m = text.match(URL_IN_TEXT)
    if (m) {
      const url = trimTrailing(m[0])
      const rest = text.replace(m[0], '').replace(/\s+/g, '')
      if (rest.length <= MAX_FRAGMENT_CHARS) return url
      return null
    }
  }

  // ③ 丢了协议的形态。**刻意只认这两种**：
  //    - `www.xxx.com…`（浏览器地址栏的典型形态，`www.` 前缀本身就是强信号）
  //    - `xxx.com/path…`（**必须有路径**）—— `README.md`、`config.yaml` 这类裸文件名
  //      没有 `/`，不会被误判；而 `foo.com` 这种无路径裸域名也放过，因为没有任何
  //      可用的信号把它和普通词区分开，宁可不认。
  //    ⚠️ 不维护 TLD 白名单：多一张表就多一处漂移，且新版 TLD 进来时会静默漏判。
  if (!/\s/.test(text) && (BARE_WWW.test(text) || BARE_WITH_PATH.test(text))) {
    return `https://${text}`
  }

  return null
}
