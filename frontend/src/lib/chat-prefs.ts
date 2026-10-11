/**
 * LiMo — 对话侧的**发送偏好**（目前一项：追问建议）。
 *
 * ⚠️ **与 `marker-prefs` / `card-prefs` 的关键区别：它不写 DOM 属性。**
 * 那两个控制「正文怎么显示」，属性挂在 `<html>` 上、由 CSS 选择器消费；
 * 这一项控制「**下一次请求带不带某个参数**」，是个**瞬时读取值** ——
 * `dispatchSend` 在发请求那一刻 `loadChatPrefs()`，不进 store slice
 * （进了就得加订阅、加迁移，而它每秒都不会变一次）。
 *
 * ⚠️ **默认 `followUp: false` = 零回归**：关着的时候 `follow_up` 参数根本不发
 * （见 `api/chat.ts::streamChat`），后端 `Query(0)` 走默认，行为与改造前逐字相同。
 *
 * ⚠️ **用户选了就必须有**（这是本功能的全部承诺）：开关打开时后端会**补调一次**
 * LLM 保证产出 —— 见 `backend/app/agent/followup.py` 的模块文档。开关打开却什么都
 * 没看到（既没卡、也没失败提示）是**不允许**出现的状态。
 *
 * ⚠️ localStorage 用**对象形态**（`mbw_chatPrefs`）而不是散装 key：对话侧偏好
 * 将来大概率不止一个，届时不必再改 key 结构（同 `reading-prefs` / `card-prefs`）。
 */

export interface ChatPrefs {
  /** `true` = 每轮回答结束后给 4～6 个可点击直发的追问建议（默认关） */
  followUp: boolean
}

/** ⚠️ 必须等于「改造前的现状」—— 改这里就是改变所有用户的行为 */
export const CHAT_DEFAULTS: ChatPrefs = { followUp: false }

const CHAT_KEY = 'mbw_chatPrefs'

/**
 * 读 localStorage + **逐字段校验**。
 * 单项越界/类型错只退回**该项**默认值，不整包丢弃（对齐 `marker-prefs` /
 * `reading-prefs` 的形态 —— 将来加第二项时不需要改结构）。
 */
export function loadChatPrefs(): ChatPrefs {
  try {
    const raw = localStorage.getItem(CHAT_KEY)
    if (raw) {
      const v = JSON.parse(raw) as Partial<ChatPrefs>
      return {
        followUp: typeof v.followUp === 'boolean' ? v.followUp : CHAT_DEFAULTS.followUp,
      }
    }
  } catch { /* 损坏 JSON → 全部用默认 */ }
  return { ...CHAT_DEFAULTS }
}

export function saveChatPrefs(p: ChatPrefs): void {
  try { localStorage.setItem(CHAT_KEY, JSON.stringify(p)) } catch { /* quota */ }
}
