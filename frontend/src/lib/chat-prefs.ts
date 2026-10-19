import { useSyncExternalStore } from 'react'

/**
 * LiMo — 对话侧的**发送偏好**（三项：联网、追问建议、回答依据）。
 *
 * ⚠️ **与 `marker-prefs` / `card-prefs` 的关键区别：它不写 DOM 属性。**
 * 那两个控制「正文怎么显示」，属性挂在 `<html>` 上、由 CSS 选择器消费；
 * 这三项控制「**下一次请求带不带某个参数**」，不发请求时谁也不看它。
 * 发送那一刻由 `dispatchSend` 直接 `loadChatPrefs()`（要的是「此刻的值」）。
 *
 * ⚠️ **不进 store slice，但**有**一个模块内的订阅器**（`useChatPrefs`，2026-10-20）：
 * 有两个入口（输入框 + 设置页）之后，「各自 `useState(loadChatPrefs())`」会让设置页改完
 * 而输入框仍显示旧值 —— 界面与发送行为不一致且看不出来。走 `useSyncExternalStore`
 * 而不是加进 zustand，是因为这里**没有跨域 action**、没有迁移需求，一个 10 行的订阅器
 * 比一个 slice 更小（判据：加 slice 是为了跨域共享，不为共享一个 localStorage 值）。
 *
 * ⚠️ **对话侧的偏好不止这一个文件**：字号缩放是 `lib/chat-font.ts`，它属于
 * 「写 DOM 变量」那一族（形态同 `marker-prefs` / `card-prefs`），故独立成文件、
 * **不要**为了「都是对话的」把它并进来（合并会让本文件也多出一个要 apply 的字段）。
 *
 * ⚠️ **三个默认值都是「等于改造前的现状」**（判据同一条，取值看起来不一样只因起点不同）：
 *
 * | 项 | 默认 | 为什么 |
 * |---|---|---|
 * | `webSearch` | **`true`** | 联网一直可用 —— 这是**给已有能力加开关**，默认关就是砍功能 |
 * | `followUp` | `false` | 追问是**新增能力**，默认开就是替所有用户改了行为 |
 * | `answerMode` | **`'strict'`** | 改造前本就是「只用知识库、无来源不说话」，默认换档就是替所有人改了回答纪律 |
 *
 * 后端侧同款：`chat.py::chat_stream` 的 `web_search: int = Query(1)`、
 * `answer_mode: str = Query("strict")` vs 产物侧 `GenerateRequest.web_search: bool = False`。
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

/**
 * 「回答依据」三档（与后端 `chat.py::_ANSWER_MODES` 同源，改一处必须改另一处）。
 *
 *  · `strict` 只用知识库 —— 两态开关时代「严格」那一档；
 *  · `open`   知识库优先，但允许补充，且必须标明哪句是补充；
 *  · `free`   自由对话，不围绕知识库（用户顺口问个事、聊个无关话题）。
 *
 * ⚠️ **`free` 是用户补充要求的**（「顺便问问的那些……需要极致的发散」）：
 * 前两档都还要求「先看库」，而「顺便问一句」的场景里那恰恰是噪音。
 * ⚠️ 三档不是「程度递增」意义上的同一根轴（`open` 是「文档优先 + 补充」，
 * `free` 是「根本不围绕文档」）—— 所以是一组枚举而不是一个数字滑条。
 */
export type AnswerMode = 'strict' | 'open' | 'free'

/**
 * 三档的**文案**（唯一一份）—— 输入框的下拉与设置页的单选组都从这里取。
 *
 * ⚠️ **只有一个事实：这三个名字必须与实际行为对得上**，所以不放在某个组件里
 * （放组件里，第二个入口就只能复制一份 —— 用户改了文案而另一处没改，两处说法打架）。
 * 图标不在数据里：设置页的单选组只用文字，输入框那三个图标由它自己映射
 * （图标是那一处的视觉细节，不是这三档的定义）。
 *
 * ⚠️ 顺序即默认优先级：`strict` 必须排第一（它是默认档）。
 */
export const ANSWER_MODE_OPTIONS: { key: AnswerMode; label: string; desc: string }[] = [
  { key: 'strict', label: '只用知识库', desc: '库里没有就说没找到，不用常识补' },
  { key: 'open', label: '允许发散', desc: '文档优先，但可以补充，会标明来源' },
  { key: 'free', label: '自由对话', desc: '随便聊，不必先查库' },
]

export interface ChatPrefs {
  /** `true` = 允许模型联网（`search_web` / `fetch_url`）；关掉则整组摘掉工具（默认开） */
  webSearch: boolean
  /** `true` = 每轮回答结束后给 4～6 个可点击直发的追问建议（默认关） */
  followUp: boolean
  /** 回答依据（默认 `strict` = 改造前的「只用知识库」） */
  answerMode: AnswerMode
}

/** ⚠️ 必须等于「改造前的现状」—— 改这里就是改变所有用户的行为 */
export const CHAT_DEFAULTS: ChatPrefs = { webSearch: true, followUp: false, answerMode: 'strict' }

const CHAT_KEY = 'mbw_chatPrefs'

/** 原始字符串 → 偏好。逐字段校验，坏值只退回**该项**默认（`null` = 没有存储） */
function parsePrefs(raw: string | null): ChatPrefs {
  if (!raw) return { ...CHAT_DEFAULTS }
  try {
    const v = JSON.parse(raw) as Partial<ChatPrefs>
    return {
      webSearch: typeof v.webSearch === 'boolean' ? v.webSearch : CHAT_DEFAULTS.webSearch,
      followUp: typeof v.followUp === 'boolean' ? v.followUp : CHAT_DEFAULTS.followUp,
      // 未知档位退回默认（**读回宽**）—— 写回是前端自己控制的枚举，
      // 唯一可能读到坏值是旧版本残留或手改 localStorage；此时落默认档比抛错安全。
      // ⚠️ 与后端 `_answer_mode`（**入口严**，非法值显式报错）刻意不同：
      // 一边是「用户刚选的值」，一边是「磁盘上可能过期的值」，判据不同，处置也就不同。
      answerMode: v.answerMode === 'open' || v.answerMode === 'free'
        ? v.answerMode
        : CHAT_DEFAULTS.answerMode,
    }
  } catch { return { ...CHAT_DEFAULTS } }   // 损坏 JSON → 全部用默认
}

// ⚠️ **缓存以「磁盘上的原始字符串」为键**，不是简单的一次性缓存：
//  · `useSyncExternalStore` 的 `getSnapshot` 必须返回**同一个引用**（每次 `parse`
//    出一个新对象 = 无限重渲染），所以要缓存；
//  · 但缓存不能无视磁盘变化 —— 测试 `localStorage.clear()` / 手改 localStorage /
//    另一个标签页写入，都必须立刻读到新值。比对字符串同时满足两者，且
//    `getItem` 是同步内存操作，一次比较的开销可以忽略。
//  · 哨兵用 `undefined`（不是 `null`）：首帧必须解析一次，而「没有存储」本身就是 `null`。
let _cachedRaw: string | null | undefined = undefined
let _cached: ChatPrefs = { ...CHAT_DEFAULTS }

/**
 * 读 localStorage + **逐字段校验**（真相源；返回稳定引用，可作 `useSyncExternalStore` 的快照）。
 * 单项越界/类型错只退回**该项**默认值，不整包丢弃（对齐 `marker-prefs` /
 * `reading-prefs` 的形态 —— 将来加第四项时不需要改结构）。
 */
export function loadChatPrefs(): ChatPrefs {
  let raw: string | null = null
  try { raw = localStorage.getItem(CHAT_KEY) } catch { /* localStorage 不可用 → 默认 */ }
  if (raw !== _cachedRaw) {
    _cachedRaw = raw
    _cached = parsePrefs(raw)
  }
  return _cached
}

const _listeners = new Set<() => void>()

function subscribeChatPrefs(fn: () => void): () => void {
  _listeners.add(fn)
  return () => { _listeners.delete(fn) }
}

/**
 * 订阅对话偏好的 hook —— **两处入口（输入框 / 设置页）看到同一份状态**的唯一办法。
 *
 * ⚠️ 为什么不是「两处各自 `useState(loadChatPrefs())`」：那样设置页改完之后，**已经挂载**
 * 的输入框仍显示旧值，而发送那一刻又读的是新值 —— 按钮说「只用知识库」、实际按「自由对话」
 * 跑，界面上完全看不出来（且这个组件在会话进行中一直挂着，不会重新挂载）。
 * 有**两个**入口就必须有**一份**订阅，这正是 `useSyncExternalStore` 的用途。
 *
 * ⚠️ 发送路径（`chatStore.dispatchSend`）**不走 hook**，它仍然在发请求那一刻调
 * `loadChatPrefs()` —— 那个位置要的是「此刻的值」，不是「渲染时的值」。
 */
export function useChatPrefs(): ChatPrefs {
  return useSyncExternalStore(subscribeChatPrefs, loadChatPrefs, loadChatPrefs)
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
  // ⚠️ 通知**两个入口**（输入框 / 设置页）。写入失败（quota）时 `loadChatPrefs` 返回的
  // 仍是缓存里那个**同一个引用**，`useSyncExternalStore` 按引用比较后不会重渲染 ——
  // 等价于「什么也没发生」，不需要在这里分支判断成功与否。
  for (const fn of _listeners) fn()
}
