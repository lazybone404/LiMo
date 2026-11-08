/**
 * 产物折叠阅读的档位偏好 —— 主题（卡片网格）/ 一句话（每节一句）/ 全文。
 *
 * ## 为什么是 localStorage 而不是 config.yaml
 *
 * 它是**本机观感**，与 `reading-prefs` / `card-prefs` / `export-prefs` 同族：不随库走、
 * 不需要后端知道、不进导出。走 config 要同时改 `DEFAULT_CONFIG` + `ConfigUpdate`
 * （`extra="forbid"`，漏了直接 422）+ GET 回显，为一个枚举开三个落点。
 *
 * ## 默认必须是「全文」
 *
 * 改造前没有折叠，默认值等于改造前现状 —— 这是零回归的判据，也是**没有报错**的那一类：
 * 默认值改成 `topic` 的话，升级后所有长文打开就只剩一排卡片，而界面上看起来「正常」。
 *
 * ⚠️ **档位是持久化的，所以消费方必须自己回落**：昨天在长文里选的主题，今天打开一篇
 * 两千字的产物，如果直接用这个值渲染就只剩几张空卡。回落判据在 `PreviewArea` 的
 * `foldActive`（流式 / 编辑中 / 短文 → 强制 `full`），不在本模块 —— 本模块只管存取。
 */

export type FoldLevel = 'topic' | 'lead' | 'full'

export interface FoldPrefs {
  level: FoldLevel
}

/** ⚠️ 改这里等于改所有新用户的首屏 —— 必须是改造前的现状（全文） */
export const FOLD_DEFAULTS: FoldPrefs = { level: 'full' }

const FOLD_KEY = 'mbw_fold'

const LEVELS: readonly FoldLevel[] = ['topic', 'lead', 'full']

/** 读 localStorage + **逐字段校验**（形态对齐 `export-prefs` / `reading-prefs`） */
export function getSavedFold(): FoldLevel {
  try {
    const raw = localStorage.getItem(FOLD_KEY)
    if (raw) {
      const v = JSON.parse(raw) as Partial<FoldPrefs>
      if (v.level && LEVELS.includes(v.level)) return v.level
    }
  } catch { /* 损坏 JSON → 默认 */ }
  return FOLD_DEFAULTS.level
}

export function saveFold(level: FoldLevel): void {
  try { localStorage.setItem(FOLD_KEY, JSON.stringify({ level })) } catch { /* quota */ }
}
