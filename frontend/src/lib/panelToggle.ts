/**
 * LiMo — 左侧面板开合的**唯一判据**（纯函数，可单测）
 *
 * 为什么单独成模块：`uiSlice` 里那三个统一入口（open / close / toggle）都要问同样三个问题
 * ——「它现在是开的吗」「打开它要带哪些补丁」「互斥下下一个激活的是谁」。写在 slice 里
 * 就只能靠跑起来看，抽出来才能直接喂假数据断言。同一手法见 `reading-prefs.nextFontScale`。
 *
 * ⚠️ **本模块与 `uiSlice` 都不得 import `slotRegistry`**。这不是风格偏好：注册表持有
 * 面板组件（`SlotDef.component`），而组件 import store → `store → uiSlice → slotRegistry`
 * 即成环。`slotMetrics` 的注释记着这条判据的原始形态（「将来会 register 面板组件」）。
 * 因此这里的判据**只依赖 `SlotState` 本身**（type-only import），不去查 `SlotDef`——
 * 左侧与否由调用方（组件层，可以安全 import 注册表）决定。
 */
import type { SlotState } from '../store/types'
import { PANEL_DEFAULT_WIDTH } from './slotMetrics'

/**
 * 一个面板当前是否「可见地开着」。
 *
 * ⚠️ 三重条件缺一不可：
 *  - `visible` —— genPanel 曾用 `visible=false` 表达「彻底从布局里移除」（不渲染，连
 *    窄条都没有），与 `collapsed`（收起为 34px 窄条）是**两种**关闭。统一入口打开时
 *    必须把两者一起复位，否则「关过一次的生成面板再也打不开」。
 *  - `collapsed` —— 用户把它拖窄（`setSlot` 在 width<80 时自动置位）或主动收起。
 *  - 互斥时还要求它就是当前激活的那个。
 */
export function isPanelOpen(
  slot: SlotState,
  exclusive: boolean,
  active: string | null,
  id: string,
): boolean {
  if (!slot.visible || slot.collapsed) return false
  return exclusive ? active === id : true
}

/**
 * 打开一个面板要打的补丁。
 *
 * ⚠️ **宽度 < 80 时必须复位**：这种宽度是用户把面板拖窄留下的，`setSlot` 见到它就自动
 * 置 `collapsed`，此后 `collapsed: false` 也只会展开出「一条 40px 的缝」。`openDocLibSearch`
 * 早就为 Ctrl+K 防过这个坑，这里把它收成所有入口共用的一条规则。
 */
export function openPatch(slot: SlotState): Partial<SlotState> {
  return {
    visible: true,
    collapsed: false,
    ...(slot.width < 80 ? { width: PANEL_DEFAULT_WIDTH } : {}),
  }
}

/**
 * 互斥模式下按下一个图标之后，谁是激活面板。
 *
 * 规则对齐 VS Code：**再按一次是关闭，不是切到另一个**（明确关了就不该悄悄露出别的
 * 面板——用户按的是「关掉这个」，不是「给我那个」）。
 * 并排模式不使用本函数（那条路走各自 `collapsed` 的普通开关），原样返回 `active`。
 */
export function nextActive(
  exclusive: boolean,
  active: string | null,
  id: string,
  isOpen: boolean,
): string | null {
  if (!exclusive) return active
  return isOpen ? null : id
}
