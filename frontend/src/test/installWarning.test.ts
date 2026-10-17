import { describe, it, expect, beforeEach, vi } from 'vitest'
import {
  installWarningText, isInstallWarningDismissed, dismissInstallWarning,
  type InstallPathWarning,
} from '../lib/installWarning'

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

describe('「关过就不再提示」的记忆（2026-10-18）', () => {
  beforeEach(() => {
    localStorage.removeItem('limo_install_warning_dismissed')
    vi.restoreAllMocks()
  })

  it('默认没关过 —— 告警照常显示', () => {
    expect(isInstallWarningDismissed()).toBe(false)
  })

  it('关过之后跨会话保持（这是它存在的理由：Rust 每次启动都会重新发事件）', () => {
    dismissInstallWarning()
    // 模拟「下次启动」：只重新读一次，不重写
    expect(isInstallWarningDismissed()).toBe(true)
    // 直接看落盘形态 —— 上一条若因为函数里藏了内存态而恒真，这里会红
    expect(localStorage.getItem('limo_install_warning_dismissed')).toBe('1')
  })

  it('重复点关闭是幂等的（按钮可被连点）', () => {
    dismissInstallWarning()
    dismissInstallWarning()
    expect(isInstallWarningDismissed()).toBe(true)
  })

  it('存储不可用时退化为「每次都提示」，而不是把告警一起吞掉', () => {
    // 失效形态：把读失败当作「已关闭」——隐私模式下用户**永远看不到**这条告警，
    // 而这恰好是安装路径真有问题的那台机器。
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied') })
    expect(isInstallWarningDismissed()).toBe(false)
  })

  it('写失败不抛错（不能因为记不住就崩在点击处理器里）', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota') })
    expect(() => dismissInstallWarning()).not.toThrow()
  })
})
