/**
 * 侧栏插槽的尺寸常量 —— **唯一真相源**。
 *
 * 为什么单独一个文件：这组值同时被两个地方需要，而两者的依赖方向本不该相连。
 *
 * ```
 * lib/slotRegistry.ts  ──注册 SlotDef.defaultWidth + component──▶  SlotLayout 渲染
 *        ▲
 *        │ ⛔ 不能 import：注册表**持有面板组件**（`SlotDef.component`），
 *        │    而组件 import store → store ← uiSlice 是它们的下游 → 环
 *        │
 * store/uiSlice.ts ──slots 初值（首渲染即用）──────▶  同一条宽度
 * ```
 *
 * ⚠️ **环的判据是「`uiSlice` 值引用 `slotRegistry`」，不是「注册表持有组件」**。
 * 2026-10-10 之前这里的说法是「将来会 register 面板组件」—— 那时组件确实硬编码在
 * `SlotLayout` 里；本轮把 `component` 收进 `SlotDef` 之后，它成真了，而结论不变：
 * 注册表可以持有组件，**store 侧不得反向依赖注册表**（`uiSlice` / `panelToggle` 只认
 * `SlotState`，不认 `SlotDef`）。
 *
 * 收口前是「同一份值在三处各写一遍 + 一句『改一处必须改另一处』的注释」，
 * 而这类同值重写的失效方式**不报错**：改了注册表没改 store 初值，症状只是
 * 「首次渲染宽度与拖拽复位后的默认宽度不一致」——拖一下就对了，看不出是 bug。
 *
 * ⚠️ 本模块**零依赖**（不 import react / 组件 / store），任何一侧都可安全 import。
 */

/** 面板展开时的默认宽度 px */
export const PANEL_DEFAULT_WIDTH = 260

/** 最小宽度 px（小于 80 由 `uiSlice.setSlot` 自动标记折叠） */
export const PANEL_MIN_WIDTH = 50

/** 最大宽度 px */
export const PANEL_MAX_WIDTH = 600
