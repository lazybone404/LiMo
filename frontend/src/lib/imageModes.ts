/**
 * 图片处理四模式的**唯一标签表**（extract / table / digitize / understand）。
 *
 * 收敛前这组中文标签在**七处**各写一遍：生成面板的模式卡与提交按钮、
 * tab 注册表的 label 与导出后缀、结果视图标题、处理中占位、预览区两处空态。
 * 中文标签手抄的失效方式很安静：改一处漏一处 = 界面上同一个功能两个名字
 * （模式卡写「文档数字化」、tab 写「文档扫描」），**零报错、零测试红**。
 *
 * ⚠️ 这里**只收标签**。同名的三份描述文案（`ImageModeCards` 的模式卡、
 * `PreviewArea` 的 result 空态）措辞**刻意不同**（一处是「选什么」的引导语、
 * 一处是「结果长什么样」的说明），且各自跟着自己的版面走，故不合并。
 * 同理 icon 是 lucide 组件，放进本模块会把图标库拉进所有 importers。
 *
 * ⚠️ 与后端 `app/routers/process.py` 的 mode 枚举成对（模式 #52）：
 * 这里少一个 key = 该模式在 tab 栏/按钮上显示英文 key；后端多一个 = 前端看不见。
 */

/** 键序即界面顺序（模式卡 2×2、tab 优先级、空态提示行都按它排） */
export const IMAGE_MODES = ['extract', 'table', 'digitize', 'understand'] as const

export type ImageMode = (typeof IMAGE_MODES)[number]

export const IMAGE_MODE_LABELS: Record<string, string> = {
  extract: '提取文字',
  table: '表格数据',
  digitize: '文档数字化',
  understand: '理解内容',
}

/** 取标签；未知 mode 原样返回 key（可见，不静默变空串） */
export function imageModeLabel(mode: string): string {
  return IMAGE_MODE_LABELS[mode] || mode
}
