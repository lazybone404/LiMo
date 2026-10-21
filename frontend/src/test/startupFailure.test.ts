import { describe, it, expect } from 'vitest'
import {
  logDisplayText, failureDetailLines,
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

  // ---- 「日志为空」有三种成因，而用户看到的都是一块空白 ----
  // 这条是「别人机器上没有任何日志」的直接回答：目录都写不进去时，
  // 空白本身就是故障，得说出来并附上目录。
  it('日志目录不可写 → 说这是失败原因，并给出目录', () => {
    const text = logDisplayText(make({
      log_tail: '', log_dir_writable: false, log_dir: 'D:\\lockdown\\logs',
    }))
    expect(text).toContain('写不进去')
    expect(text).toContain('D:\\lockdown\\logs')
  })

  it('目录可写但两份日志都没生成 → 指向 DLL / 运行库 / 杀软隔离', () => {
    const text = logDisplayText(make({
      log_tail: '', log_dir_writable: true,
      launcher_log_exists: false, backend_log_exists: false,
    }))
    expect(text).toContain('都没留下任何输出')
    expect(text).toContain('VC++')
  })

  // 对照实验：旧版 Rust 不带这三个自检字段，undefined 不能被当成 false ——
  // 否则老启动器 + 新前端会误报「日志目录写不进去」，把用户引去查一个
  // 根本不存在的问题。
  it('旧版启动器不带自检字段 → 走原兜底，不误报不可写', () => {
    const text = logDisplayText(make({ log_tail: '' }))
    expect(text).not.toContain('写不进去')
    expect(text).toContain('没有留下任何错误输出')
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
