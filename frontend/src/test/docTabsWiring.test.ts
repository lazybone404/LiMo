/**
 * 标签栏与字号缩放的**接线**断言（源码级）。
 *
 * 为什么需要这一层：`docTabs.test.ts` 覆盖的是 store 动作本身，`docTabBar.test.tsx`
 * 覆盖的是组件渲染，但**「store 动作有没有被真正接上去」谁都没测**——这正是本项目
 * #51 号模式（「能力存在 ≠ 该路径在用它」）：动作写得再完整，`loadDocContent` 里漏了
 * 那行调用，界面上的症状就是「文档显示了、标签栏空的」，而全部 store 测试照样全绿。
 *
 * 集成测试（渲染 `PreviewArea`）的代价是 `vi.mock` 掉一整个依赖图，脆弱且慢；
 * 而这三处接线**判的是位置关系**（谁在谁之前），源码级断言恰好够用且更稳。
 * 先例见 `readingLayout.test.tsx` 对 `index.css` 的断言。
 *
 * 对照实验：
 *   A. 把 `commitTabChange` 挪到 `if (!docId) return` 之后 → 第 1 条红。
 *      症状是「关闭最后一个标签」提交不了（那条路径的 docId 正是 null）。
 *   B. 去掉 `{!immersive && …}` 守卫 → 第 3 条红（沉浸模式里留一排点不动的噪音）。
 *   C. `addEventListener('wheel', h, { passive: false })` 换成 JSX 的 `onWheel={…}`
 *      → 第 4 条红。React 按 passive 注册 root 监听，`preventDefault()` 在里面无效，
 *      结果是 WebView2 缩放整个应用（连文档库、工具栏一起）。
 */
/// <reference types="node" />
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

/**
 * 读源码并**剥掉注释**。剥注释不是洁癖：这些函数的文档注释里刻意引用了
 * `if (!docId) return` 与 `onWheel` 等字样当反例，不剥的话「谁在谁之前」的
 * 位置断言会被注释里的同名字样带偏（假红/假绿都出现过）。
 */
function src(rel: string): string {
  return readFileSync(resolve(process.cwd(), rel), 'utf-8')
    .replace(/\/\*[\s\S]*?\*\//g, '')   // 块注释 / JSDoc / JSX 注释
    .replace(/^\s*\/\/.*$/gm, '')       // 整行行注释（`^\s*` 前缀保证不误伤字符串里的 //）
}

describe('PreviewArea 的标签接线', () => {
  const preview = src('src/components/preview/PreviewArea.tsx')

  it('⚠️ commitTabChange 在 loadDocContent 内、且在 `if (!docId) return` 之前', () => {
    // 对照 A：挪到早退之后 → 这条红。
    // 「关闭最后一个标签」走的正是 docId = null 那条路径，那时同样要提交 pendingClose
    const commits = [...preview.matchAll(/commitTabChange\(/g)]
    const earlyReturns = [...preview.matchAll(/if \(!docId\) return/g)]
    expect(commits).toHaveLength(1)        // 自证：唯一，不然下面的下标比较没有意义
    expect(earlyReturns).toHaveLength(1)
    expect(commits[0].index!).toBeLessThan(earlyReturns[0].index!)
  })

  it('handleDialogCancel 里调 cancelTabChange（「取消」= 什么都没发生）', () => {
    const fn = preview.match(/const handleDialogCancel = \(\) => \{([\s\S]*?)\n  \}/)
    expect(fn).toBeTruthy()                // 自证：切片真的取到了函数体
    expect(fn![1]).toMatch(/cancelTabChange\(\)/)
  })

  it('DocTabBar 渲染带 `!immersive` 守卫', () => {
    // 对照 B：沉浸模式会同时隐藏文档库，没有打开新文档的入口，标签栏留着只是噪音
    expect(preview).toMatch(/\{!immersive && <DocTabBar \/>\}/)
  })
})

describe('PreviewArea 的字号接线', () => {
  const preview = src('src/components/preview/PreviewArea.tsx')

  it('滚轮走原生监听 + `{ passive: false }`，且**没有** React 的 onWheel', () => {
    // 对照 C：换成 `onWheel={…}` → 这条红
    expect(preview).toMatch(/addEventListener\('wheel', \w+, \{ passive: false \}\)/)
    expect(preview).toMatch(/removeEventListener\('wheel', \w+\)/)   // 有挂必有摘
    expect(preview).not.toMatch(/onWheel=/)
  })

  it('版宽换算走 measurePxOf（与字号同源），不再是内联的 × 16', () => {
    // 漏乘 fontScale 的症状是**方向反的**：字号放大后「每行 40 字」算出 640px，
    // 而实际每行只放得下 25 字 —— 越放大越挤
    expect(preview).toMatch(/measurePxOf\(reading\.measure, reading\.fontScale\)/)
  })
})
