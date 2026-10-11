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
 * | **骨架** `skeleton` | **要不要**按标记显示卡片 | `paper`（一整篇连续正文，默认=现状）／`card`（**模型标记过的**章节成卡） |
 * | **视觉方案** `style` | 卡片**长什么样** | 8 套（`gpt` 默认） |
 *
 * ⚠️ **`card` 不是「每个 `##` 一张卡」**（2026-10-11 起）：用户判定「一律按标题切卡太
 * 简单粗暴，卡片应该用在需要强调的地方」。哪几节成卡由**模型**在产物里标
 * （`<!-- limo-block:v1 type="card" -->` 写在标题之下），其余章节保持普通正文。
 * 骨架轴因此只回答**「要不要按标记显示卡片」**，不回答「哪些节该成卡」——
 * 后者是 `lib/markdown-sections.ts::splitByH2` 的 `Section.card`。
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
  /** 产物骨架。`paper` = 现状（一整篇连续长文），`card` = **被标记的**章节成卡 */
  skeleton: Skeleton
  /** 卡片视觉方案。⚠️ `skeleton === 'paper'` 时该值不生效（DOM 上不写该属性） */
  style: CardStyle
  /** 逐维度微调 —— 底色基色。`null` = **跟随视觉方案**（默认） */
  bg: string | null
  /** 逐维度微调 —— 抬升 0–100。`null` = 跟随视觉方案 */
  lift: number | null
  /** 逐维度微调 —— 描边。`null` = 跟随视觉方案 */
  border: string | null
}

/**
 * 逐维度微调的三根旋钮 —— **与 8 套方案是同一批 CSS 变量的两个入口**（模式 #54）。
 *
 * 为什么值得做：8 套预设**覆盖不到**用户想要的全部组合。最典型的是「抬升」——
 * 8 套里只有 `0%`（gpt/paper/guofeng/seal/ink/outline 六个）与 `100%`（apple/compact）
 * 两个取值，中间一整段（用户 2026-10-10 原话「在背景颜色基础上稍微做点变化」）
 * **一格都没有**。底色与它是同一根轴的两端（`--card-bg = color-mix(base, elevated lift)`），
 * 描边则多出「粗中性边」「主色细边」两档。
 *
 * ⚠️ **`css` 字段是「key → CSS 值」的唯一映射**，导出时前端把**解析好的值**发给后端
 * （`serializeCardVars`），后端只做安全过滤、**不持有第二份表** —— 否则就是本项目的
 * 「同一份判据住在两处」，格式演进时漏改一侧静默失效。
 */
export const CARD_BG_OPTIONS: { key: string; label: string; css: string }[] = [
  { key: 'surface', label: '页面底', css: 'var(--tk-surface)' },
  { key: 'hover', label: '悬停面', css: 'var(--tk-hover)' },
  { key: 'accent', label: '主色淡底', css: 'color-mix(in srgb, var(--tk-accent) 8%, var(--tk-elevated))' },
  { key: 'transparent', label: '透明', css: 'transparent' },
]

export const CARD_BORDER_OPTIONS: { key: string; label: string; css: string }[] = [
  { key: 'light', label: '极细浅', css: '1px solid var(--tk-border-light)' },
  { key: 'base', label: '标准', css: '1px solid var(--tk-border)' },
  { key: 'thick', label: '粗描边', css: '2px solid var(--tk-border)' },
  { key: 'accent', label: '主色细边', css: '1px solid var(--tk-accent)' },
  { key: 'accent-thick', label: '主色粗边', css: '2px solid var(--tk-accent)' },
  { key: 'none', label: '无描边', css: 'none' },
]

/**
 * 受控变量清单 —— **唯一一份**。`applyCard`（写 DOM）与 `serializeCardVars`
 * （拼导出参数）都从这里派生，不各写一份字面量。
 */
export const CARD_VARS = {
  bg: '--card-bg-base',
  lift: '--card-lift',
  border: '--card-border',
} as const

const BG_KEYS = CARD_BG_OPTIONS.map(o => o.key)
const BORDER_KEYS = CARD_BORDER_OPTIONS.map(o => o.key)

/** 该档基色的 CSS 值 —— 不认识的值返回 `undefined`（调用方按「跟随方案」处理）。 */
function bgCss(key: string | null): string | undefined {
  return CARD_BG_OPTIONS.find(o => o.key === key)?.css
}
function borderCss(key: string | null): string | undefined {
  return CARD_BORDER_OPTIONS.find(o => o.key === key)?.css
}

/**
 * 「抬升」此刻**能不能调** —— 基色是透明时它拉不出正确结果。
 *
 * ⚠️ 原理：`--card-bg` 是 `color-mix(in srgb, var(--card-bg-base), var(--tk-elevated) var(--card-lift))`，
 * 而 `color-mix` 里的 `transparent` 等价于 `rgb(0 0 0 / 0)` —— 抬升把它混出来的是
 * **半透明黑**，不是「在背景色上做变化」。故透明基色 + 非零抬升是一个**无效组合**
 * （模式 #53：不留拉不出正确结果的旋钮）。处置是**强制抬升为 0**、UI 上禁用滑条。
 *
 * ⚠️ 「跟随方案」时方案取值是运行时才知道的（`outline` 就是透明底），故读**运行时**
 * 的 `--card-bg-base`。⚠️ 必须先由 `applyCard` 清空 inline 变量再调本函数，否则读到的是
 * 自己上一轮写进去的值（自证循环）。判据是 `=== 'transparent'` 而非解析成颜色：
 * 关键字会被 computed style **原样返回**，而 `var(--tk-surface)` 会被解析成 `rgb(...)`
 * —— 这一条恰好把两个来源区分开了。
 */
export function isLiftDisabled(p: Pick<CardPrefs, 'bg'>): boolean {
  if (p.bg) return p.bg === 'transparent'
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue(CARD_VARS.bg).trim()
    return v === 'transparent'
  } catch { return false }
}

/**
 * 拼成导出侧要的「变量覆盖串」（`--a:1;--b:2`），**没有任何显式微调时返回空串**。
 *
 * ⚠️ **传的是解析好的 CSS 值而不是 key**：后端因此只做字符白名单校验、不认语义 ——
 * 它要是自己再维护一张「key → 值」表，就是同一份判据的第二份实现（格式演进时
 * 漏改一侧的症状是「预览对了、导出还是老样子」，且导出**照样成功**、看不出异常）。
 */
export function serializeCardVars(p: CardPrefs): string {
  const out: string[] = []
  const bg = bgCss(p.bg)
  if (bg) out.push(`${CARD_VARS.bg}:${bg}`)
  // ⚠️ 判据是**显式的** `bg === 'transparent'`，**刻意不调 `isLiftDisabled`** ——
  // 那个函数要读运行时 DOM（为了认「跟随方案时的 outline 也是透明底」），而本函数是
  // 纯函数：一旦读 DOM，同一次导出里的结果就取决于调用时刻的 `<html>` 上残留了什么，
  // 测试之间也会互相污染。那条路径已由两道机制堵死（切方案清空微调 + UI 禁用滑条）。
  // ⚠️ 透明档要**主动写 0%** 而不是「不写」：不写就会让 `apple`（方案自带 100%）
  // 那套在透明底下混出半透明黑 —— 出口侧也是同一个坑（`applyCard` 同款处置）。
  if (p.bg === 'transparent') out.push(`${CARD_VARS.lift}:0%`)
  else if (p.lift !== null) out.push(`${CARD_VARS.lift}:${p.lift}%`)
  const bd = borderCss(p.border)
  if (bd) out.push(`${CARD_VARS.border}:${bd}`)
  return out.join(';')
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
  // ⚠️ 三项微调默认**全部为 `null`（跟随视觉方案）**：新的旋钮不得改变任何既有用户的
  // 默认观感。用户不动它们时，DOM 上一个 inline 变量都不写，8 套方案的取值原样生效。
  bg: null,
  lift: null,
  border: null,
}

export const SKELETON_OPTIONS: { key: Skeleton; label: string; hint: string }[] = [
  { key: 'paper', label: '整篇', hint: '内容连续排下来，标题只是标题。长文阅读的传统形态' },
  { key: 'card', label: '卡片', hint: '模型标为重点的章节渲染成卡片，其余章节保持普通正文' },
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
        // ⚠️ 三项微调同样**逐字段校验**（`null` 合法 = 跟随方案）。读到不认识的值
        // 退回 `null` 而不是原样透传 —— 透传出去的表现是「存了但永远不生效」。
        bg: typeof v.bg === 'string' && BG_KEYS.includes(v.bg) ? v.bg : null,
        lift: typeof v.lift === 'number' && v.lift >= 0 && v.lift <= 100 ? v.lift : null,
        border: typeof v.border === 'string' && BORDER_KEYS.includes(v.border) ? v.border : null,
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
  // ⚠️⚠️ **第一步无条件清空全部受控变量**，且必须在任何读取之前 ——
  // ① 切回 `paper` 时若留着上一轮的 inline 覆盖，下次改方案会被它们压住（静默不生效）；
  // ② `isLiftDisabled` 读的是运行时 `--card-bg-base`，不清空就会读到**自己上一轮写的值**
  //    （自证循环：一旦选了透明就再也回不去）。
  // 清单从 `CARD_VARS` 派生 —— 不手写第二份字面量（模式 #54）。
  for (const v of Object.values(CARD_VARS)) root.style.removeProperty(v)
  if (p.skeleton !== 'card') {
    // ⚠️ removeAttribute 而非写 "paper" —— 见文件头「零回归靠选择器不匹配」。
    root.removeAttribute('data-skeleton')
    root.removeAttribute('data-card-style')
    return
  }
  root.setAttribute('data-skeleton', 'card')
  root.setAttribute('data-card-style', p.style)

  // 逐维度微调 —— inline 写在 `<html>` 上，特异性天然压过 `[data-card-style="x"]`
  // 方案块（非 inline），故「用户调过的」覆盖「方案给的」，未调的字段一个字节不写、
  // 原样落回方案取值。
  const bg = bgCss(p.bg)
  if (bg) root.style.setProperty(CARD_VARS.bg, bg)
  const bd = borderCss(p.border)
  if (bd) root.style.setProperty(CARD_VARS.border, bd)
  // ⚠️ 抬升单独一支：透明基色下**强制归零并覆盖方案值**（不是「不写」）——
  // `outline` 方案自己定义的抬升若是 100%，不写就等于放任那个无效组合生效。
  const liftOff = isLiftDisabled(p)
  if (liftOff) root.style.setProperty(CARD_VARS.lift, '0%')
  else if (p.lift !== null) root.style.setProperty(CARD_VARS.lift, `${p.lift}%`)
}

export function saveCard(p: CardPrefs): void {
  try { localStorage.setItem(CARD_KEY, JSON.stringify(p)) } catch { /* quota */ }
}
