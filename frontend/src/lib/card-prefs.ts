/**
 * LiMo — 产物骨架轴 + 卡片视觉方案轴
 *
 * 与六轴的关系：**不是第七轴**。六轴描述「界面长什么样」，这两根轴描述「产物正文怎么组织」。
 * 放在同一族（themeSlice）只因为它们与 texture / font 一样是「影响预览观感的外观设置」。
 * ⚠️ **刻意不进 `THEME_PRESETS`** —— 换一个主题包不该把产物骨架连带重置（与阅读版式
 * 「关乎阅读生理，不该被换主题包连带重置」同一条理由）。
 *
 * 两根轴正交：
 *
 * | 轴 | 管什么 | 取值 |
 * |---|---|---|
 * | **骨架** `skeleton` | **要不要**卡片 | `paper`（一整张纸，默认=现状）／`card`（每节一张卡） |
 * | **视觉方案** `style` | 卡片**长什么样** | 8 套（`gpt` 默认） |
 *
 * ⚠️ **骨架 = `paper` 时视觉方案轴失效**（没有卡片可画）—— 设置页必须**隐藏**该控件，
 * 否则是一个「调了没反应」的死旋钮（模式 #53）。
 *
 * ⚠️⚠️ **零回归靠「选择器不匹配」而不是「取值相同」**：`paper` 时 `applyCard()` 把两个
 * 属性都 `removeAttribute`，于是 `cards.css` 里所有 `[data-skeleton="card"] …` 规则
 * **永远命不中**。若写成 `data-skeleton="paper"`，则任何一条漏写作用域前缀的卡片规则
 * 都会漏出来 —— 那是「看起来对、其实错」的形态。属性缺失即 paper 是**结构性**保证。
 *
 * ⚠️ Skeleton 轴换的是**渲染**，不改变产物 Markdown 的结构要求 —— prompt 管「产物有没有
 * 结构」（`_NOTES_TASK` 等无条件要求 `##`），骨架管「结构长什么样」。故风格档里
 * 不该出现任何关于「要不要标题」的条件句（见 notes_styles.py `plain` 档第 4 条）。
 */

export type Skeleton = 'paper' | 'card'

export type CardStyle =
  | 'gpt' | 'apple' | 'compact' | 'outline' | 'paper'   // 现代 5
  | 'guofeng' | 'seal' | 'ink'                          // 国风 3

export interface CardPrefs {
  /** 产物骨架。`paper` = 现状（一整篇连续长文），`card` = 每个 H2 一张卡 */
  skeleton: Skeleton
  /** 卡片视觉方案。⚠️ `skeleton === 'paper'` 时该值不生效（DOM 上不写该属性） */
  style: CardStyle
}

/**
 * ⚠️ **两项默认值共同构成「改造前逐像素一致」** —— 改这里就是改变所有既有用户的默认观感。
 * `skeleton: 'paper'` 尤其不能想当然写成 `'card'`（「新功能当然要开」）：
 * 那会让每一个既有用户打开应用就看到陌生的排版，且零报错、零提示。
 * 由 `cardPrefs.test.ts` 钉住。
 */
export const CARD_DEFAULTS: CardPrefs = {
  skeleton: 'paper',
  style: 'gpt',
}

export const SKELETON_OPTIONS: { key: Skeleton; label: string; hint: string }[] = [
  { key: 'paper', label: '整篇', hint: '内容连续排下来，标题只是标题。长文阅读的传统形态' },
  { key: 'card', label: '卡片', hint: '每个二级标题（##）单独成一张卡，节与节之间有明确边界' },
]

/**
 * 8 套视觉方案的标签。**顺序即设置页的渲染顺序**，`gpt` 在第一位（默认档）。
 * ⚠️ 取值全部来自 [design/cards.md §0.7](../../../design/cards.md) 与实物样板，
 * **本文件只负责标签，不负责取值** —— 取值住在 `styles/cards.css`（单一真相源，
 * 后端导出侧读同一份文件）。
 */
export const CARD_STYLE_OPTIONS: { key: CardStyle; label: string; hint: string }[] = [
  { key: 'gpt', label: 'GPT 风', hint: '描边分层、几乎无阴影、大留白。默认观感' },
  { key: 'apple', label: '苹果风', hint: '投影分层，靠阴影而非描边把卡片托起来' },
  { key: 'compact', label: '紧凑', hint: '小圆角 + 密内边距，同样篇幅里塞进更多内容' },
  { key: 'outline', label: '描边', hint: '底色透明，只有一圈描边勾勒边界' },
  { key: 'paper', label: '纸张', hint: '与页面同色、小圆角，像一张压在纸面上的便签' },
  { key: 'guofeng', label: '国风双框', hint: '方角 + 内外两圈细线，印谱与线装书格线的语汇' },
  { key: 'seal', label: '印章', hint: '主色粗边 + 淡主色底 + 方角。换 accent 即是换印泥颜色' },
  { key: 'ink', label: '水墨', hint: '无框无圆角，只靠大留白与字号分层' },
]

const CARD_KEY = 'mbw_card'

const SKELETONS: Skeleton[] = ['paper', 'card']
const STYLES: CardStyle[] = CARD_STYLE_OPTIONS.map(o => o.key)

/**
 * 读 localStorage + **逐字段校验**（照 `reading-prefs.ts::getSavedReading` 的纪律）。
 * 单项非法只退回**该项**默认值，不整包丢弃 —— 一个坏字段不该带走另一个。
 *
 * ⚠️ `skel` 取自 `SKELETONS` 而不是内联两个字符串字面量：合法集必须与
 * `applyCard()` 认得的集**同源**，否则「读出来是合法的、写 DOM 时却不认识」。
 */
export function getSavedCard(): CardPrefs {
  try {
    const raw = localStorage.getItem(CARD_KEY)
    if (raw) {
      const v = JSON.parse(raw) as Partial<CardPrefs>
      return {
        skeleton: SKELETONS.includes(v.skeleton as Skeleton)
          ? v.skeleton as Skeleton
          : CARD_DEFAULTS.skeleton,
        style: STYLES.includes(v.style as CardStyle)
          ? v.style as CardStyle
          : CARD_DEFAULTS.style,
      }
    }
  } catch { /* 损坏 JSON → 全部用默认 */ }
  return { ...CARD_DEFAULTS }
}

/**
 * 写 DOM —— **两个属性名必须与 `styles/cards.css` 的选择器、以及导出 HTML 上写的
 * 属性名完全一致**。不一致时不报错，只是样式永远不生效（本轮最容易静默失效的一处）。
 */
export function applyCard(p: CardPrefs): void {
  const root = document.documentElement
  if (p.skeleton !== 'card') {
    // ⚠️ removeAttribute 而非写 "paper" —— 见文件头「零回归靠选择器不匹配」。
    // 若将来加逐维度微调（inline 写 `--card-*`），这里也要一并清空。
    root.removeAttribute('data-skeleton')
    root.removeAttribute('data-card-style')
    return
  }
  root.setAttribute('data-skeleton', 'card')
  root.setAttribute('data-card-style', p.style)
}

export function saveCard(p: CardPrefs): void {
  try { localStorage.setItem(CARD_KEY, JSON.stringify(p)) } catch { /* quota */ }
}
