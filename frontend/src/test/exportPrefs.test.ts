/**
 * 导出行为偏好 —— 「下载 MD 要不要连图片一起带走」（= 要不要依赖后端）。
 *
 * **这一项为什么值得有一个开关**：快捷「下载 MD」从前是**纯前端**的
 * （直接把 store 里的正文落盘），而正文里的图片引用是 `/api/assets/{id}/x.png`
 * 这种只在本应用内有效的路径 —— 文件一离开应用就整篇裂图。要自包含就只能让后端
 * 把图片 base64 内嵌进来，代价是这个动作从「零依赖」变成「后端必须在跑」。
 * 两边的取舍用户自己定，默认取「带图」（`embedImages: true`）。
 *
 * 对照实验：
 *   A. `EXPORT_DEFAULTS.embedImages` 顺手写成 `false`（「保守一点」）→ 红。
 *      后果是默认回到裂图，而用户不会知道这是默认值而非 bug。
 *   B. 非布尔值被当成 falsy 接受（`p.embedImages ?? true` 而不是 typeof 校验）→
 *      localStorage 里一个 `"false"` 字符串会**静默关掉**内嵌 → 红。
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { EXPORT_DEFAULTS, getSavedExport, saveExport } from '../lib/export-prefs'

const KEY = 'mbw_export'

beforeEach(() => localStorage.clear())

describe('默认值', () => {
  it('默认**把图片一起带走**（= 默认依赖后端）', () => {
    // 对照 A：写成 false → 默认回到裂图，且界面上看不出异常
    expect(EXPORT_DEFAULTS.embedImages).toBe(true)
  })

  it('无存储时返回默认', () => {
    expect(getSavedExport()).toEqual(EXPORT_DEFAULTS)
  })
})

describe('读写往返与容错', () => {
  it('save → get 往返一致', () => {
    saveExport({ embedImages: false })
    expect(getSavedExport()).toEqual({ embedImages: false })
  })

  it('损坏的 JSON → 落回默认（不抛）', () => {
    localStorage.setItem(KEY, '{不是 json')
    expect(getSavedExport()).toEqual(EXPORT_DEFAULTS)
  })

  it('非布尔值 → 落回默认（而不是被当成 falsy 接受）', () => {
    // 对照 B：`"false"` 是字符串，若直接 `?? ` 兜底会被原样收下
    localStorage.setItem(KEY, JSON.stringify({ embedImages: 'false' }))
    expect(getSavedExport().embedImages).toBe(true)
  })

  it('缺字段 → 只补这一个字段，不整体丢弃', () => {
    localStorage.setItem(KEY, JSON.stringify({}))
    expect(getSavedExport()).toEqual(EXPORT_DEFAULTS)
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// 跨语言成对守卫：这条偏好最终落到 `GET /export/{id}?format=md&image_md=inline`
// ─────────────────────────────────────────────────────────────────────────────
//
// ⚠️ 只做**源码级**守卫，不做组件行为测试：`ExportMenu` 从 store 读十来个字段，
// 渲染它要铺一整套 mock，而这一条要守的其实只是「那个查询参数名与后端认的对得上」
// —— 项目对导出侧一贯的做法（见 `exportMarkers.test.ts`）。
//
// 对照实验：改掉任一侧的参数名（`image_md` → `imageMd`，或后端 `Query("link")` 改名）
// → 红。两侧不同源时的症状是**静默返回旧行为**（不带图的 md，HTTP 200）。

describe('跨语言 — 前端发的参数与后端认的参数同源', () => {
  const menu = readFileSync(
    resolve(process.cwd(), 'src/components/ui/ExportMenu.tsx'), 'utf-8')
  const backend = readFileSync(
    resolve(process.cwd(), '../backend/app/routers/export.py'), 'utf-8')

  it('ExportMenu 在「内嵌」这条路上带 image_md=inline', () => {
    expect(menu).toContain("image_md: 'inline'")
  })

  it('后端 export_content 有这个参数，且**默认是 link**（零回归）', () => {
    const m = backend.match(/image_md:\s*str\s*=\s*Query\(\s*"(\w+)"/)
    expect(m, 'backend 里找不到 image_md 的 Query 定义（改名了？）').toBeTruthy()
    expect(m![1]).toBe('link')
  })

  it('前端两条路径给出的文件名**同源**（同一动作不该因开关改名）', () => {
    // ⚠️ 后端那条命名是 `{标题}-{mode}.md`，不含版本后缀与日期，与前端
    // `makeFileName` 不同源 —— 故走后端时必须显式 `preferSuggestedName: true`，
    // 否则「同一个按钮，换个开关文件名就变了」。
    expect(menu).toContain('preferSuggestedName: true')
  })
})
