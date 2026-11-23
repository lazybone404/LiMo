/**
 * 「深度联网」的偏好层（`mbw_chatPrefs.deepWeb`）。
 *
 * 要防的失效全是静默的：
 *
 *   A. 默认值不是 `false` → 存量用户一开对话就换了检索实现（服务端检索、无本地工具），
 *      而界面只在按钮上多高亮一项，没人会发现。
 *   B. 读回时坏值把**整包**偏好打回默认 → 「深度联网的记录坏了，顺手把联网开关也开了」。
 *   C. 保存时整对象覆盖 → 「开了追问，深度联网自己关了」。
 *
 * ⚠️ 这个文件只管**偏好本身**。「什么条件下才真的发 `deep_web=1`」是另一层闸门
 * （与 capabilities 同源），住在 `chatInputDeepWeb.test.tsx` —— 两层刻意分开测：
 * 偏好为真而能力不支持时**必须不发**，合并成一个断言就分不清是哪一层拦下的。
 */
import { describe, it, expect, beforeEach } from 'vitest'
import {
  loadChatPrefs, saveChatPrefs, CHAT_DEFAULTS,
  effectiveWebMode, saveWebMode, webModeOf,
} from '../lib/chat-prefs'

const PREFS_KEY = 'mbw_chatPrefs'

beforeEach(() => { localStorage.clear() })

describe('深度联网偏好', () => {
  it('默认关闭（新增能力 + 换了检索实现，默认开 = 替所有人改行为）', () => {
    expect(loadChatPrefs().deepWeb).toBe(false)
    expect(CHAT_DEFAULTS.deepWeb).toBe(false)
  })

  it('能存能读（字段名是磁盘契约，改了就认不出旧记录）', () => {
    saveChatPrefs({ deepWeb: true })
    expect(loadChatPrefs().deepWeb).toBe(true)
    expect(JSON.parse(localStorage.getItem(PREFS_KEY)!).deepWeb).toBe(true)

    saveChatPrefs({ deepWeb: false })
    expect(loadChatPrefs().deepWeb).toBe(false)
  })

  it('⚠️ 合并式保存：改这一项不重置另外三项', () => {
    saveChatPrefs({ webSearch: false, followUp: true, answerMode: 'free' })
    saveChatPrefs({ deepWeb: true })

    const p = loadChatPrefs()
    expect(p.deepWeb).toBe(true)
    expect(p.webSearch).toBe(false)
    expect(p.followUp).toBe(true)
    expect(p.answerMode).toBe('free')

    // 反向：改别的字段不该把深度联网打回默认
    saveChatPrefs({ answerMode: 'open' })
    expect(loadChatPrefs().deepWeb).toBe(true)
  })

  it('坏值只退回该项默认，另一项照常保留', () => {
    for (const bad of ['yes', 1, null, {}, []]) {
      localStorage.setItem(PREFS_KEY, JSON.stringify({ deepWeb: bad, webSearch: false }))
      expect(loadChatPrefs().deepWeb).toBe(false)
      expect(loadChatPrefs().webSearch).toBe(false)
    }
  })

  it('损坏 JSON → 全部回默认、不抛', () => {
    localStorage.setItem(PREFS_KEY, '{这不是 JSON')
    expect(loadChatPrefs().deepWeb).toBe(false)
  })
})

// ═══════════════════════════════════════════════════════════
// 三态 ↔ 两个布尔量（2026-11-24：界面合成一个三选一，存储不动）
//
// 存储仍是两个布尔量（改它要迁移老用户的 localStorage，而「不出现无效组合」
// 这件事由下面两个函数保证）。界面与发送侧都只认三态。
// ═══════════════════════════════════════════════════════════

describe('联网三态', () => {
  it('webModeOf：三个合法组合各归各档', () => {
    expect(webModeOf({ ...CHAT_DEFAULTS, webSearch: false, deepWeb: false })).toBe('off')
    expect(webModeOf({ ...CHAT_DEFAULTS, webSearch: true, deepWeb: false })).toBe('local')
    expect(webModeOf({ ...CHAT_DEFAULTS, webSearch: true, deepWeb: true })).toBe('server')
  })

  it('⚠️ 无效组合（关了联网却开着 deepWeb）归一成 off —— `off` 优先', () => {
    // 老版本或手改 localStorage 都可能留下它。若归一成 server，界面会显示
    // 「服务端检索已选中」，而这一轮其实一个网络请求都不会发。
    expect(webModeOf({ ...CHAT_DEFAULTS, webSearch: false, deepWeb: true })).toBe('off')
  })

  it('saveWebMode：一次写两个键，不留残局', () => {
    saveWebMode('off')
    expect(loadChatPrefs()).toMatchObject({ webSearch: false, deepWeb: false })
    saveWebMode('local')
    expect(loadChatPrefs()).toMatchObject({ webSearch: true, deepWeb: false })
    saveWebMode('server')
    expect(loadChatPrefs()).toMatchObject({ webSearch: true, deepWeb: true })
  })

  it('effectiveWebMode：模型不支持时 server 退回 local，其余原样', () => {
    expect(effectiveWebMode('server', true)).toBe('server')
    expect(effectiveWebMode('server', false)).toBe('local')
    expect(effectiveWebMode('local', true)).toBe('local')
    expect(effectiveWebMode('off', true)).toBe('off')
  })
})
