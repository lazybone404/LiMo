/**
 * LiMo — 主题轴注册表（六轴中的五条**选择型**轴）
 *
 * ## 它解决什么
 *
 * 加一条轴此前要改 5~6 处：轴模块 / store 初始 state / store setter / 启动链 apply /
 * 一键主题包 `applyPreset` / 设置页 UI。**漏掉启动链那一处的表现是「冷启动不生效、
 * 零报错」** —— 值在 store 里、DOM 上没写（这个坑已踩过三次：`--texture-opacity` /
 * `reading` / `card`，三处注释都在记它）。
 *
 * 现在：**轴模块 + 本文件一行**。启动链与预设遍历都从这张表派生。
 *
 * ## 边界（三条，都不是遗漏）
 *
 * - **纹理强度不进表**：它是**数值轴**（0-100 滑条），而本表的形状是
 *   「离散选项 + membership 校验」。硬塞进来会得到一个没有 `options` 的轴，
 *   每个消费点都得 `if` 一次 —— 那不是注册表，是垃圾桶。
 *   ⇒ 它在启动链里单独一行（[store/index.ts](../store/index.ts)）。
 * - **阅读版式 / 骨架卡片 / 标记装饰不进表**：它们不是「界面六轴」，且**刻意不进
 *   `THEME_PRESETS`**（换主题包不该连带重置阅读生理设置与正文组织方式）。
 * - **`apply` / `save` 引用各模块的函数，不在这里重新实现**：合法性白名单
 *   （`getSavedX` 里的 `options.some(...)`）**只许有一份**。本文件再写一份
 *   = 模式 #54（同源漂移：两处都「看起来对」，改一处漏一处的失效是静默的）。
 */

import { THEME_OPTIONS, getSavedTheme, resolveTheme, applyTheme, saveTheme, type ThemeMode } from './theme'
import { ACCENT_OPTIONS, getSavedAccent, applyAccent, saveAccent, type AccentColor } from './accent'
import { BASE_OPTIONS, getSavedBase, applyBase, saveBase, type BasePreset } from './theme-base'
import {
  TEXTURE_OPTIONS, FONT_OPTIONS,
  getSavedTexture, applyTexture, saveTexture, type TexturePreset,
  getSavedFont, applyFont, saveFont, type FontPreset,
} from './texture'

export interface ThemeAxis {
  /**
   * 轴标识 —— **同时是 store 字段名与 `THEME_PRESETS` 的字段名**（三者同名是刻意的）。
   * 于是 `applyPreset` 的判据可以写成 `if (!(axis.key in p)) continue`，
   * 不需要为「预设里有哪些轴」再造第四张表。
   */
  key: 'theme' | 'accent' | 'base' | 'texture' | 'font'

  /**
   * DOM 属性名（`<html data-accent="…">`）。
   * ⚠️ 它同时是**跨语言守卫的锚**：`themeAxisSync.test.ts` 拿它去
   * `styles/theme-layer.css` 里找 `[data-x="…"]` 选择器并比对 key 集合 ——
   * 「JS 里能选的 key」与「CSS 里实现了的 key」必须一致，否则用户选中一套
   * 没有 CSS 的配色时**界面纹丝不动、零报错**。
   */
  attr: string

  /** 该轴的全部合法取值 —— 真相源（设置页 / 白名单 / 一致性守卫都读它）。 */
  options: readonly { key: string; label: string }[]

  apply(v: string): void
  saved(): string
  save(v: string): void

  /**
   * 该轴要在 store 里写的字段。默认 `{ [key]: v }`（字段名 == `key`）。
   * 明暗轴**多一个派生字段** `resolvedTheme`，故单独给。
   *
   * ⚠️ 返回裸记录而不是 `Partial<Store>`：本文件刻意**不 import `Store` 类型**
   * （那会与 `store/types.ts` 成环）。字段名的正确性由 `themeSlice` 的消费端
   * 与 `themeAxisSync.test.ts` 共同保证。
   */
  state?(v: string): Record<string, unknown>
}

/**
 * ⚠️ **顺序 = 启动链的 apply 顺序**（与改造前的手写链逐行同序）。
 * 五条之间**无依赖**（各自只写自己的属性），故顺序其实不重要；保持同序纯粹是
 * 为了让这轮改造的 diff 可读、review 时不必论证「换个顺序会不会有影响」。
 *
 * ⚠️ 唯一的隐式耦合是 `applyOnAccent()` —— 它由 `applyTheme` 与 `applyAccent`
 * **各自**在结尾调用（P1-16 实底前景色）。两条都调是刻意的：只挂一处的话，
 * 切「跟随系统」时 accent 没变、变的是 data-theme，前景色会停在上一个明暗档。
 *
 * ⚠️ 下面每条轴的 `apply` / `save` 都写成了 lambda —— 参数位置是**逆变**的，
 * 不能直接抄 `apply: applyAccent`（那是 `(v: AccentColor) => void`，不接受任意
 * `string`）。注册表的入参由 `options` 保证合法，这些 lambda 只做断言不做校验。
 */
export const THEME_AXES: ThemeAxis[] = [
  {
    key: 'theme',
    attr: 'data-theme',
    options: THEME_OPTIONS,
    // 明暗轴是五条里唯一带特化的：DOM 上要设的是**解析后**的 light/dark
    // （用户存的是 'system'，而 'system' 不是合法的 data-theme 取值），
    // `applyTheme` 内部还顺手写 `style.colorScheme` 并重算 on-accent 前景色。
    apply: v => applyTheme(resolveTheme(v as ThemeMode)),
    saved: getSavedTheme,
    save: v => saveTheme(v as ThemeMode),
    state: v => ({ theme: v, resolvedTheme: resolveTheme(v as ThemeMode) }),
  },
  {
    key: 'accent',
    attr: 'data-accent',
    options: ACCENT_OPTIONS,   // 扁平表；设置页的分组视图是 ACCENT_GROUPS（同一份数据）
    apply: v => applyAccent(v as AccentColor),
    saved: getSavedAccent,
    save: v => saveAccent(v as AccentColor),
  },
  {
    key: 'base',
    attr: 'data-base',
    options: BASE_OPTIONS,
    apply: v => applyBase(v as BasePreset),
    saved: getSavedBase,
    save: v => saveBase(v as BasePreset),
  },
  {
    key: 'texture',
    attr: 'data-texture',
    options: TEXTURE_OPTIONS,
    apply: v => applyTexture(v as TexturePreset),
    saved: getSavedTexture,
    save: v => saveTexture(v as TexturePreset),
  },
  {
    key: 'font',
    attr: 'data-font',
    options: FONT_OPTIONS,
    apply: v => applyFont(v as FontPreset),
    saved: getSavedFont,
    save: v => saveFont(v as FontPreset),
  },
]

/**
 * 按 key 取轴 —— 消费方写 `AXIS.accent` 而不是 `THEME_AXES[1]`：
 * 后者把「轴的顺序」变成一个隐式契约，往表里插一条新轴就会**静默指错轴**。
 */
export const AXIS = Object.fromEntries(THEME_AXES.map(a => [a.key, a])) as Record<ThemeAxis['key'], ThemeAxis>

