import { describe, it, expect } from 'vitest'
import {
  isFatalStartupFailure, logDisplayText, failureDetailLines,
  exitCodeHint, installPathWarning, diagnosticsText,
  type StartupFailure,
} from '../lib/startupFailure'

function make(over: Partial<StartupFailure> = {}): StartupFailure {
  return {
    stage: 'exited',
    summary: '后端进程启动后立即退出（退出码 1）',
    alive: false,
    exit_code: 1,
    log_tail: '[启动器] LiMo dir=C:\\Program Files\\LiMo\n[启动器] spawning ...\n[后端] Traceback ...\nModuleNotFoundError',
    log_path: 'C:\\Users\\u\\AppData\\Local\\LiMo\\logs\\limo_backend_stderr.log',
    exe_path: 'C:\\Program Files\\LiMo\\LiMoBackend.exe',
    port_occupied: false,
    install_dir_writable: true,
    ...over,
  }
}

describe('isFatalStartupFailure', () => {
  it('进程已退出 → 致命（不进主界面）', () => {
    expect(isFatalStartupFailure(make({ alive: false }))).toBe(true)
  })

  // ---- 对照实验：判据必须是 alive，不是「收到了失败事件」 ----
  // 进程还在跑只是慢，放行进去至少能看缓存内容；把它判成致命会让「首次启动慢
  // 一点的机器」直接卡在失败屏上 —— 那是比原行为更糟的回归。
  it('进程仍存活（timeout）→ 不致命', () => {
    expect(isFatalStartupFailure(make({ stage: 'timeout', alive: true, exit_code: null }))).toBe(false)
  })

  it('没有诊断信息 → 不致命', () => {
    expect(isFatalStartupFailure(null)).toBe(false)
    expect(isFatalStartupFailure(undefined)).toBe(false)
  })
})

describe('logDisplayText', () => {
  it('有输出时原样返回（去首尾空白）', () => {
    expect(logDisplayText(make({ log_tail: '  boom  \n' }))).toBe('boom')
  })

  // 「两段日志一行都没有」是 DLL 缺失 / 运行库缺失 / 杀软拦截的典型形态 ——
  // 留白会让用户以为界面坏了，说清楚才能指向下一步动作。
  it('无输出时给出可行动的解释，而不是空串', () => {
    const text = logDisplayText(make({ log_tail: '   ' }))
    expect(text.length).toBeGreaterThan(0)
    expect(text).toContain('没有留下任何错误输出')
  })

  // 中文路径是「一行日志都没有」的成因之一：它必须在兜底文案里点名，
  // 否则用户会照着「装运行库」试一圈而问题在目录名上。
  it('无输出时的兜底文案点名安装路径', () => {
    expect(logDisplayText(make({ log_tail: '' }))).toContain('安装路径')
  })

  // 启动器日志必须在场 —— 它是「exe 存在但被拒绝访问」这类故障的唯一记录。
  it('保留启动器那一行的来源前缀', () => {
    expect(logDisplayText(make())).toContain('[启动器]')
  })
})

describe('exitCodeHint', () => {
  // 这些故障 stdout/stderr 一个字都没有，退出码是唯一线索。
  it('0xC0000135 翻译成「缺 VC++ 运行库」', () => {
    expect(exitCodeHint(-1073741515)).toContain('VC++ 运行库')
  })

  it('0xC000007B 翻译成架构/DLL 冲突', () => {
    expect(exitCodeHint(-1073741701)).toContain('架构')
  })

  // 对照实验：普通退出码（1）不该被硬套上一条 NTSTATUS 解释 ——
  // 那会让用户去查一个根本不存在的问题。
  it('普通退出码不返回解读', () => {
    expect(exitCodeHint(1)).toBe('')
    expect(exitCodeHint(0)).toBe('')
  })

  it('没有退出码时返回空串', () => {
    expect(exitCodeHint(null)).toBe('')
    expect(exitCodeHint(undefined)).toBe('')
  })
})

describe('failureDetailLines', () => {
  it('列出退出码、程序路径与日志路径', () => {
    const lines = failureDetailLines(make())
    expect(lines.some(l => l.includes('退出码：1'))).toBe(true)
    expect(lines.some(l => l.includes('LiMoBackend.exe'))).toBe(true)
    expect(lines.some(l => l.includes('limo_backend_stderr.log'))).toBe(true)
  })

  it('端口占用只在真占用时出现', () => {
    expect(failureDetailLines(make({ port_occupied: false })).some(l => l.includes('7413'))).toBe(false)
    expect(failureDetailLines(make({ port_occupied: true })).some(l => l.includes('7413'))).toBe(true)
  })

  it('没有退出码时不虚构一行「退出码：null」', () => {
    const lines = failureDetailLines(make({ exit_code: null, alive: true, stage: 'timeout' }))
    expect(lines.some(l => l.includes('退出码'))).toBe(false)
  })

  it('已知的 NTSTATUS 退出码后面跟着人话解读', () => {
    const lines = failureDetailLines(make({ exit_code: -1073741515 }))
    const line = lines.find(l => l.includes('退出码'))!
    expect(line).toContain('VC++ 运行库')
  })
})

describe('installPathWarning', () => {
  it('路径干净且可写 → 空串（不渲染警告条）', () => {
    expect(installPathWarning(make({ exe_path: 'C:\\LiMo\\LiMoBackend.exe', install_dir_writable: true }))).toBe('')
  })

  // 中文用户名会让**默认**安装路径（%LOCALAPPDATA%\Programs\LiMo）含非 ASCII ——
  // 这条判据与顶栏横幅同源（都来自 analyze_install_path 的口径）。
  it('路径含中文 → 提示重装到纯英文目录', () => {
    const text = installPathWarning(make({ exe_path: 'C:\\Users\\张三\\AppData\\Local\\LiMo\\LiMoBackend.exe' }))
    expect(text).toContain('非 ASCII')
    expect(text).toContain('重装')
  })

  it('路径含空格 → 提示', () => {
    expect(installPathWarning(make({ exe_path: 'C:\\Program Files\\LiMo\\LiMoBackend.exe' }))).toContain('空格')
  })

  it('目录不可写 → 提示', () => {
    expect(installPathWarning(make({ exe_path: 'C:\\LiMo\\LiMoBackend.exe', install_dir_writable: false }))).toContain('不可写')
  })
})

describe('diagnosticsText', () => {
  // 用户要把这一整块发给开发者 —— 结论、路径、退出码解读、日志一个都不能少。
  it('包含结论、路径、日志与退出码解读', () => {
    const text = diagnosticsText(make({ exit_code: -1073741515 }))
    expect(text).toContain('后端进程启动后立即退出')
    expect(text).toContain('LiMoBackend.exe')
    expect(text).toContain('VC++ 运行库')
    expect(text).toContain('[后端] Traceback')
  })

  it('路径有问题时把安装路径那条也带上', () => {
    const text = diagnosticsText(make({ exe_path: 'C:\\Users\\张三\\LiMo\\LiMoBackend.exe' }))
    expect(text).toContain('安装路径')
    expect(text).toContain('非 ASCII')
  })

  it('路径没问题时不追加空的「安装路径：」行', () => {
    const text = diagnosticsText(make({ exe_path: 'C:\\LiMo\\LiMoBackend.exe', install_dir_writable: true }))
    expect(text).not.toContain('安装路径：')
  })
})
