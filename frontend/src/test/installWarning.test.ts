import { describe, it, expect } from 'vitest'
import { installWarningText, type InstallPathWarning } from '../lib/installWarning'

/** 干爽的基线：三个问题位全 false。每个用例只改自己关心的那位。 */
const base: InstallPathWarning = {
  exe_path: 'C:/LiMo/LiMo.exe',
  path_has_non_ascii: false,
  path_has_space: false,
  parent_writable: true,
  platform: 'windows',
}

describe('P2-10 — 安装路径告警文案（backend-warning 的消费端）', () => {
  it('三项都无问题时返回空串 —— 调用方据此**不渲染**横幅', () => {
    // ⚠️ 这是本模块与「一律给句话」的分界：返回一句「一切正常」会让 App.tsx
    // 无条件渲染一条无信息量的横幅（模式 #53）。
    expect(installWarningText(base)).toBe('')
  })

  it('路径含空格 → 点出「路径含空格」', () => {
    expect(installWarningText({ ...base, path_has_space: true })).toContain('路径含空格')
  })

  it('路径含非 ASCII → 点出「非 ASCII」并举例中文', () => {
    const t = installWarningText({ ...base, path_has_non_ascii: true })
    expect(t).toContain('非 ASCII')
    expect(t).toContain('中文')
  })

  it('安装目录不可写 → 点出「不可写」', () => {
    expect(installWarningText({ ...base, parent_writable: false })).toContain('不可写')
  })

  it('多项同时命中 → 全部列出（不是只报第一个）', () => {
    const t = installWarningText({ ...base, path_has_space: true, parent_writable: false })
    expect(t).toContain('路径含空格')
    expect(t).toContain('不可写')
  })

  it('任何命中都带可行动建议（换安装目录），而不是只说「有问题」', () => {
    const t = installWarningText({ ...base, path_has_space: true })
    expect(t).toContain('安装')
  })
})
