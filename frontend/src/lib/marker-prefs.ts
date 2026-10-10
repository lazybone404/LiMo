/**
 * LiMo — 产物语义标记的**装饰开关**（`limo-block` 协议）。
 *
 * ⚠️ **开关的语义是「隐藏标记产生的附加物」，不是「隐藏标记」。**
 * 内容**一律保留**：`decor: false` 时块内的文字照常显示，只是不再有左边框与淡底。
 * 这是为将来那类「与内容无关的占位文本」（图表/OCR 失败标记）留的同一个口子 ——
 * 那类标记的「附加物」就是整段占位文本本身，而这个开关的判据（附加物 vs 内容）
 * 不需要改。（口子的形态见 CLAUDE.md「口子原则」。）
 *
 * 与六轴/骨架的关系：**都不是**。六轴描述界面、骨架描述章节组织，这一项描述
 * 「语义标记要不要显形」。同族放在 themeSlice 只因为它同样是「影响预览观感的外观设置」。
 * ⚠️ **刻意不进 `THEME_PRESETS`**：换一个主题包不该把标记装饰连带重置。
 *
 * ⚠️ **默认值 = 现状**：`decor: true` 且 `applyMarkers()` 在默认值时**移除属性** ——
 * 于是 `blocks.css` 里带 `[data-marker-decor="off"]` 前缀的规则**永远不匹配**。
 * 这与 `card-prefs` 的「属性缺失即 paper」是同一个零回归机制：比「取值相同」强，
 * 因为任何一条漏写前缀的规则都不可能漏出来。
 * 见 `test/markerPrefs.test.ts` —— 零回归是被断言钉住的，不是靠自觉。
 */

export interface MarkerPrefs {
  /** `true` = 显示强调块的左边框/淡底；`false` = 只留内容，隐藏装饰 */
  decor: boolean
}

/** ⚠️ 必须等于「改造前的现状」—— 改这里就是改变所有用户的默认观感 */
export const MARKER_DEFAULTS: MarkerPrefs = { decor: true }

const MARKER_KEY = 'mbw_markers'

/**
 * 读 localStorage + **逐字段校验**。
 * 单项越界/类型错只退回**该项**默认值，不整包丢弃（目前只有一个字段，但形态对齐
 * `reading-prefs` —— 将来加第二项时不需要改结构）。
 */
export function getSavedMarkers(): MarkerPrefs {
  try {
    const raw = localStorage.getItem(MARKER_KEY)
    if (raw) {
      const v = JSON.parse(raw) as Partial<MarkerPrefs>
      return {
        decor: typeof v.decor === 'boolean' ? v.decor : MARKER_DEFAULTS.decor,
      }
    }
  } catch { /* 损坏 JSON → 全部用默认 */ }
  return { ...MARKER_DEFAULTS }
}

/**
 * 写 DOM。
 *
 * ⚠️ **默认值不写属性，而是移除它** —— 「属性缺失即开」是零回归的机制（见文件头）。
 * 若写成 `data-marker-decor="on"`，`blocks.css` 的默认规则虽然也生效，但
 * 「关掉再打开」会留下一个与初始状态不同的 DOM，断言与实机核对就都得记住两种形态。
 */
export function applyMarkers(p: MarkerPrefs): void {
  const root = document.documentElement
  if (p.decor) root.removeAttribute('data-marker-decor')
  else root.setAttribute('data-marker-decor', 'off')
}

export function saveMarkers(p: MarkerPrefs): void {
  try { localStorage.setItem(MARKER_KEY, JSON.stringify(p)) } catch { /* quota */ }
}
