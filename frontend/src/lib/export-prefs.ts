/**
 * LiMo — 导出行为偏好：快捷「下载 MD」要不要把图片一起带走。
 *
 * ## 这个开关解决什么
 *
 * 快捷导出（预览区工具栏的下拉）里，**MD / 复制**走的是客户端：直接把 store 里
 * 已有的正文文本落盘（`ExportMenu.tsx` 的文件头注释第 3 条），**从头到尾不碰图片
 * 文件**。而正文里的图片引用是 `/api/assets/{文档id}/xxx.png` 这种根相对路径 ——
 * 只在 LiMo 自己渲染时才有意义。于是导出的 `.md` 一离开应用（关掉后端、发给别人）
 * 就整篇裂图。
 *
 * 图片本体一直躺在库目录 `{文档}/assets/` 里 —— 要跟着走，就得**有人去盘上把它们
 * 读出来**，那件事只有后端做得到（HTML / Word / PDF 内嵌、ZIP 收进 `images/` 都是
 * 后端干的）。
 *
 * ## 两个取值
 *
 * - `true`（**默认**）：下载 MD 走 `GET /export/{id}?format=md&image_md=inline`，
 *   图片以 base64 直接写进 `.md`，**单文件自包含**，离线 / 转发都能看。
 *   代价是**依赖本地后端在运行**（Tauri 下后端是随应用起的，正常不会不在）。
 * - `false`：保持改造前的纯前端路径，图片只留链接。不依赖后端，但文件离开 LiMo 裂图。
 *
 * ⚠️ **只管「下载 MD」**，不管「复制 Markdown」：剪贴板里塞几百 KB base64 不是
 * 任何人想要的东西，而复制这个动作的典型去向（粘进聊天框、粘进别的编辑器）本来
 * 也不需要图片跟着走。这条分工写在设置页的说明文案里，别让用户自己猜。
 *
 * ⚠️ **只管快捷导出**，不管详细导出面板：那边本来就有「图片：独立文件夹 / base64
 * 内嵌 / 不包含」三选一（`ExportPanel.tsx`），用户逐次自选，不需要一个全局默认。
 *
 * ⚠️ **默认值 = 依赖后端**（2026-10-11 用户拍板：「默认依赖」）——「导出的文件打开
 * 就能看」比「少一次后端调用」重要得多，而后者在正常使用里根本感知不到。
 */

export interface ExportPrefs {
  /** `true` = 下载 MD 时把图片 base64 内嵌（**依赖后端**）；`false` = 只写链接（纯前端） */
  embedImages: boolean
}

/** ⚠️ 改这里等于改所有新用户的导出行为 —— 见文件头「默认依赖」的理由 */
export const EXPORT_DEFAULTS: ExportPrefs = { embedImages: true }

const EXPORT_KEY = 'mbw_export'

/**
 * 读 localStorage + **逐字段校验**（形态对齐 `marker-prefs` / `reading-prefs`）：
 * 单项类型不对只退回该项默认值，不整包丢弃 —— 将来加第二个字段时不必改结构。
 */
export function getSavedExport(): ExportPrefs {
  try {
    const raw = localStorage.getItem(EXPORT_KEY)
    if (raw) {
      const v = JSON.parse(raw) as Partial<ExportPrefs>
      return {
        embedImages: typeof v.embedImages === 'boolean' ? v.embedImages : EXPORT_DEFAULTS.embedImages,
      }
    }
  } catch { /* 损坏 JSON → 全部用默认 */ }
  return { ...EXPORT_DEFAULTS }
}

export function saveExport(p: ExportPrefs): void {
  try { localStorage.setItem(EXPORT_KEY, JSON.stringify(p)) } catch { /* quota */ }
}
