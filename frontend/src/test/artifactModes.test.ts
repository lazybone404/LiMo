import { describe, it, expect } from 'vitest'
import { planArtifactRequests } from '../lib/artifactModes'

/**
 * `undefined`（未知）与 `[]`（确定没有）的分流是本模块存在的全部理由。
 * 写成 `modes?.includes(m) ?? …` 的简写会让旧后端下**所有文档都不发产物请求**
 * ——笔记、整理版全部不可见且零报错。故两条轴各占一组断言，缺一不可。
 */
describe('planArtifactRequests — 产物请求门控', () => {
  it('undefined（未知）→ 两个都请求：不知道就别替用户下结论', () => {
    expect(planArtifactRequests(undefined)).toEqual({ notes: true, polished: true })
  })

  it('[]（确定没有产物）→ 两个都不请求', () => {
    expect(planArtifactRequests([])).toEqual({ notes: false, polished: false })
  })

  it('只有 notes → 只请求 notes', () => {
    expect(planArtifactRequests(['notes'])).toEqual({ notes: true, polished: false })
  })

  it('只有 polished → 只请求 polished', () => {
    expect(planArtifactRequests(['polished'])).toEqual({ notes: false, polished: true })
  })

  it('两个都有 → 两个都请求', () => {
    expect(planArtifactRequests(['notes', 'polished'])).toEqual({ notes: true, polished: true })
  })

  it('不认识的值不误伤：多余 mode 不影响已知两个的判定', () => {
    expect(planArtifactRequests(['notes', 'extract'])).toEqual({ notes: true, polished: false })
  })

  // ⚠️ 对照实验（写死期望值，与上一组**成对**）：把 `undefined` 与 `[]` 混为一谈的
  // 实现（`modes?.includes(m)` 返回 `boolean | undefined`）会让下面这条红——
  // 它正是「旧后端下笔记集体消失」那个致命静默失败的判据。
  it('对照：undefined 与 [] 的结论**必须不同**', () => {
    expect(planArtifactRequests(undefined)).not.toEqual(planArtifactRequests([]))
  })
})
