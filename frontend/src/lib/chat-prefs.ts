/**
 * LiMo — 对话侧的**发送偏好**（两项：联网、追问建议）。
 *
 * ⚠️ **与 `marker-prefs` / `card-prefs` 的关键区别：它不写 DOM 属性。**
 * 那两个控制「正文怎么显示」，属性挂在 `<html>` 上、由 CSS 选择器消费；
 * 这两项控制「**下一次请求带不带某个参数**」，是**瞬时读取值** ——
 * `dispatchSend` 在发请求那一刻 `loadChatPrefs()`，不进 store slice
 * （进了就得加订阅、加迁移，而它每秒都不会变一次）。
 *
 * ⚠️ **对话侧的偏好不止这一个文件**：字号缩放是 `lib/chat-font.ts`，它属于
 * 「写 DOM 变量」那一族（形态同 `marker-prefs` / `card-prefs`），故独立成文件、
 * **不要**为了「都是对话的」把它并进来（合并会让本文件也多出一个要 apply 的字段）。
 *
 * ⚠️ **两个默认值的方向相反，且都是「等于改造前的现状」**：
 *
 * | 项 | 默认 | 为什么 |
 * |---|---|---|
 * | `webSearch` | **`true`** | 联网一直可用 —— 这是**给已有能力加开关**，默认关就是砍功能 |
 * | `followUp` | `false` | 追问是**新增能力**，默认开就是替所有用户改了行为 |
 *
 * 判据是同一条（默认值必须等于改造前的行为），结论相反是因为两件事的起点不同。
 * 后端侧同款不对称：`chat.py::chat_stream` 的 `web_search: int = Query(1)` vs
 * 产物侧 `GenerateRequest.web_search: bool = False`。
 *
 * ⚠️ **参数只在「偏离默认」时才发出去**（不是发 `=0` / `=1`）：少一个参数是
 * 「与改造前逐字相同」里**可被断言**的那种说法（见 `api/chat.ts::streamChat`）。
 *
 * ⚠️ **用户选了就必须有**（追问的承诺）：开关打开时后端会**补调一次** LLM 保证产出
 * —— 见 `backend/app/agent/followup.py` 的模块文档。开关打开却什么都没看到（既没卡、
 * 也没失败提示）是**不允许**出现的状态。联网开关没有这条承诺 —— 它只是「允许」，
 * 模型用不用仍由它自己判断。
 *
 * ⚠️ localStorage 用**对象形态**（`mbw_chatPrefs`）而不是散装 key：对话侧偏好
 * 将来大概率不止一个，届时不必再改 key 结构（同 `reading-prefs` / `card-prefs`）。
 */

export interface ChatPrefs {
  /** `true` = 允许模型联网（`search_web` / `fetch_url`）；关掉则整组摘掉工具（默认开） */
  webSearch: boolean
  /** `true` = 每轮回答结束后给 4～6 个可点击直发的追问建议（默认关） */
  followUp: boolean
}

/** ⚠️ 必须等于「改造前的现状」—— 改这里就是改变所有用户的行为 */
export const CHAT_DEFAULTS: ChatPrefs = { webSearch: true, followUp: false }

const CHAT_KEY = 'mbw_chatPrefs'

/**
 * 读 localStorage + **逐字段校验**。
 * 单项越界/类型错只退回**该项**默认值，不整包丢弃（对齐 `marker-prefs` /
 * `reading-prefs` 的形态 —— 将来加第三项时不需要改结构）。
 */
export function loadChatPrefs(): ChatPrefs {
  try {
    const raw = localStorage.getItem(CHAT_KEY)
    if (raw) {
      const v = JSON.parse(raw) as Partial<ChatPrefs>
      return {
        webSearch: typeof v.webSearch === 'boolean' ? v.webSearch : CHAT_DEFAULTS.webSearch,
        followUp: typeof v.followUp === 'boolean' ? v.followUp : CHAT_DEFAULTS.followUp,
      }
    }
  } catch { /* 损坏 JSON → 全部用默认 */ }
  return { ...CHAT_DEFAULTS }
}

/**
 * 保存（**合并式**，参数是 `Partial`）。
 *
 * ⚠️ **签名是 `Partial` 不是 `ChatPrefs`，这是刻意的**：早先它是整对象覆盖，
 * 于是「改甲字段」的调用点必须自己记得展开 `{ ...loadChatPrefs(), 甲 }` ——
 * 而忘了展开的后果是**另一个开关被静默重置回默认值**（用户的联网开关会在切换
 * 追问开关时自己打开），既没报错也没提示。把合并放进函数内部，这类坑就不存在了
 * （判据：让**正确的写法**成为**最省事的写法**）。
 */
export function saveChatPrefs(p: Partial<ChatPrefs>): void {
  try { localStorage.setItem(CHAT_KEY, JSON.stringify({ ...loadChatPrefs(), ...p })) } catch { /* quota */ }
}
