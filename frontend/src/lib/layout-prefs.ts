/**
 * LiMo — 侧栏布局偏好
 *
 * 与六轴的关系：**不是第七轴**。六轴描述「界面长什么样」，这里描述「面板怎么摆」。
 * 单独成文件而不是塞进 `themeSlice`：它不写 DOM（没有 `apply*`），
 * 且消费方是 `uiSlice`（slots 就在那里），放主题 slice 会让两处互相绕。
 *
 * ⚠️ **默认 = 互斥切换**（2026-10-10 用户拍板）：左侧同一时刻只显示一个面板，
 * 由「活动栏」图标切换；改造前的「两个面板并排」保留为可选设置。
 * 默认值改在这里 = 改所有新用户的默认布局，与 `reading-prefs` 的「默认即现状」相反
 * ——那次是零回归，这次是**有意的默认变更**。
 *
 * 本模块**零依赖**（不 import react / 组件 / store），故 `uiSlice` 可安全 import
 * ——这正是 `slotRegistry` 做不到的那件事（见 `panelToggle.ts` 的说明）。
 */

export interface LayoutPrefs {
  /**
   * `true` = 左侧面板互斥（同一时刻只有一个展开）；`false` = 允许并排。
   *
   * ⚠️ 互斥的实现在 `uiSlice.activeLeftPanel`（一个**选择器**字段），不是
   * 「打开 A 时遍历同侧把 B 收起」——后者可被任何直接改单个 slot 的调用点绕过
   * （Ctrl+K 搜索、右键菜单、面板自己的关闭按钮），是「多入口漂移」的经典形态。
   */
  leftPanelsExclusive: boolean
}

export const LAYOUT_DEFAULTS: LayoutPrefs = {
  leftPanelsExclusive: true,
}

const LAYOUT_KEY = 'mbw_layout'

/**
 * 读 localStorage + **逐字段校验**（与 `reading-prefs.getSavedReading` 同一手法）。
 * 单项类型错只退回**该项**默认值，不整包丢弃 —— 且必须逐字段写，
 * 不能 `{...DEFAULTS, ...JSON.parse(raw)}`：那样一个 `"leftPanelsExclusive": "yes"`
 * 会被原样带进来（truthy 字符串），而 `"no"` 同样 truthy —— 浅合并看起来能用，
 * 实际是把坏值当成了合法值。
 */
export function getSavedLayout(): LayoutPrefs {
  try {
    const raw = localStorage.getItem(LAYOUT_KEY)
    if (raw) {
      const v = JSON.parse(raw) as Partial<LayoutPrefs>
      return {
        leftPanelsExclusive: typeof v.leftPanelsExclusive === 'boolean'
          ? v.leftPanelsExclusive
          : LAYOUT_DEFAULTS.leftPanelsExclusive,
      }
    }
  } catch { /* 损坏 JSON → 全部用默认 */ }
  return { ...LAYOUT_DEFAULTS }
}

export function saveLayout(p: LayoutPrefs): void {
  try { localStorage.setItem(LAYOUT_KEY, JSON.stringify(p)) } catch { /* quota */ }
}

/**
 * 设置页的选项表（与 `PARA_STYLE_OPTIONS` 同族：选项文案住在定义处，不散在组件里）。
 * `key` 直接用布尔值 —— 消费方渲染时的 React key 用 `String(o.key)`。
 */
export const LAYOUT_OPTIONS: { key: boolean; label: string; hint: string }[] = [
  {
    key: true,
    label: '互斥切换',
    hint: '同一时刻只展开一个面板，用左侧图标切换。界面更清爽（默认）',
  },
  {
    key: false,
    label: '允许并排',
    hint: '生成面板与文档库可以同时展开，各自可收起为窄条',
  },
]
