/**
 * 快捷键清单 —— **面向用户**的那一份，帮助面板的唯一数据源。
 *
 * ⚠️ 它是**展示表**，不是执行表的替代品。真正干活的是 `App.tsx::handleAppKeyDown`
 * 的 `switch (e.key)` 与各组件里的局部监听；这里只负责「说得对」。
 * 两者的关系由 `test/shortcutHelp.test.ts` 单向核对：**App.tsx 认的键必须在表里**
 * （漏一个 = 帮助面板少说一条，零报错）。反向不强制 —— 局部快捷键要不要收录是
 * 产品取舍，不是漂移。
 *
 * ⚠️ `keys` 存的是 `e.key` 的**字面量**（小写字母、`'/'`、`'\\'`、`'Escape'`），
 * 不是键位名：`'+'` 是 Shift+`=` 的 `e.key`，与 `'='` 是**同一个物理键的两个取值**，
 * 故一个动作可以带多个 keys。请一律写真实取值，别写成 `'Ctrl+G'` 这种展示串 ——
 * 展示由 `formatShortcut()` 负责，两件事混在一起，守卫就核对不上了。
 *
 * 唯一例外是滚轮手势（`keys: ['滚轮']`）：它没有 `e.key`，守卫也只做单向核对，不受影响。
 */

export type ShortcutGroup = 'global' | 'edit' | 'docLib' | 'chat'

export interface ShortcutDef {
  /** `e.key` 字面量；同一动作的等价键写多条 */
  keys: string[]
  mods: ('ctrl' | 'shift')[]
  label: string
  group: ShortcutGroup
}

/** 分组顺序即展示顺序 */
export const GROUP_ORDER: ShortcutGroup[] = ['global', 'edit', 'docLib', 'chat']

export const GROUP_LABELS: Record<ShortcutGroup, string> = {
  global: '全局',
  edit: '阅读与字号',
  docLib: '文档库',
  chat: 'AI 对话',
}

/** 只收录全局 + 高频局部 —— 不追求穷尽（局部键会随代码演进增加，表追不上是正常的） */
export const SHORTCUTS: ShortcutDef[] = [
  // ── 全局 ──
  // `'?'` 是 Shift+`/` 的 e.key —— 与 `=`/`+` 同款，两个都列才算「按住 Shift 也能开」
  { keys: ['/', '?'], mods: ['ctrl'], label: '打开/关闭这个快捷键一览', group: 'global' },
  { keys: ['g'], mods: ['ctrl'], label: '打开/关闭生成面板', group: 'global' },
  { keys: ['k'], mods: ['ctrl'], label: '展开文档库并聚焦搜索框', group: 'global' },
  { keys: ['i'], mods: ['ctrl'], label: '打开/关闭 AI 对话', group: 'global' },
  { keys: ['w'], mods: ['ctrl'], label: '切换阅读宽度', group: 'global' },
  { keys: ['\\'], mods: ['ctrl'], label: '沉浸阅读（隐藏侧栏与顶栏）', group: 'global' },
  { keys: ['Escape'], mods: [], label: '退出沉浸阅读', group: 'global' },

  // ── 阅读与字号 ──
  { keys: ['=', '+'], mods: ['ctrl'], label: '放大正文字号', group: 'edit' },
  { keys: ['-', '_'], mods: ['ctrl'], label: '缩小正文字号', group: 'edit' },
  { keys: ['0'], mods: ['ctrl'], label: '正文字号恢复默认', group: 'edit' },
  { keys: ['滚轮'], mods: ['ctrl'], label: '调整正文字号（光标在预览区）', group: 'edit' },

  // ── 文档库 ──
  { keys: ['a'], mods: ['ctrl'], label: '全选当前筛选结果（含未加载项）', group: 'docLib' },
  { keys: ['Escape'], mods: [], label: '清空选择', group: 'docLib' },
  { keys: ['拖动'], mods: ['ctrl'], label: '按住 Ctrl 拖动 = 刷选一段', group: 'docLib' },

  // ── AI 对话 ──
  { keys: ['滚轮'], mods: ['ctrl'], label: '调整对话字号（光标在消息区）', group: 'chat' },
  { keys: ['Enter'], mods: ['ctrl'], label: '保存正在编辑的消息', group: 'chat' },
]

/** 特殊键的展示名；其余单字母统一大写 */
const KEY_LABELS: Record<string, string> = {
  Escape: 'Esc',
  Enter: 'Enter',
  '\\': '\\',
}

const MOD_LABELS: Record<'ctrl' | 'shift', string> = {
  ctrl: 'Ctrl',
  shift: 'Shift',
}

/** 一个 e.key 取值 → 展示名 */
export function keyLabel(key: string): string {
  const special = KEY_LABELS[key]
  if (special) return special
  // 单字母（g / a / k …）展示成大写；数字与符号原样
  return /^[a-z]$/.test(key) ? key.toUpperCase() : key
}

/**
 * 拼成 `Ctrl+G` / `Ctrl+=` / `Ctrl+滚轮`。
 *
 * ⚠️ 一个动作有多个等价键时（`=` / `+`）**只展示第一个** —— 展示成
 * `Ctrl+= / Ctrl++` 是噪音，而用户按哪一个都行。
 */
export function formatShortcut(def: ShortcutDef): string {
  return [...def.mods.map(m => MOD_LABELS[m]), keyLabel(def.keys[0])].join('+')
}
