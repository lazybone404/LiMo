/**
 * 后端启动失败的诊断信息 —— 由 Rust `backend-startup-failed` / `backend-startup-timeout`
 * 事件（或 `get_backend_startup_failure` 命令）带过来。
 *
 * ⚠️ 这个模块存在的理由是一条真实的排查死角：后端 **stderr 其实一直在写文件**
 * （`lib.rs::spawn_backend` 把它重定向到 `%LOCALAPPDATA%\LiMo\logs\limo_backend_stderr.log`），
 * 但界面上此前只有一句「后端就绪信号超时，强制加载」，随后满屏 ERR_CONNECTION_REFUSED。
 * 用户既不知道有日志、也不知道该看哪个文件 —— 「有日志」与「够得到日志」是两件事。
 *
 * 字段与 Rust `StartupFailure` 逐字对应；`stage` 决定界面上说什么。
 */
import { installWarningText } from './installWarning'

export interface StartupFailure {
  /** `spawn`（进程都没创建出来）/ `exited`（起来后死掉）/ `timeout`（还活着但没就绪） */
  stage: 'spawn' | 'exited' | 'timeout' | string
  /** 给用户看的一句话结论（Rust 侧组装，含端口占用、exe 缺失等推断） */
  summary: string
  /** 后端进程此刻是否还活着 */
  alive: boolean
  exit_code: number | null
  /**
   * **两段日志的尾部**，行首带 `[启动器]` / `[后端]` 前缀（空串 = 两侧都没留下输出）。
   *
   * ⚠️ 不只是后端 stderr：启动器那份记着「在哪个目录、拉起哪个 exe、为什么拉不起来」，
   * 而后端 exe 如果是被 DLL 缺失杀掉，它自己是一行都不会打印的 —— 只读后端日志
   * 恰好会在最需要证据的时候空白。
   */
  log_tail: string
  log_path: string
  /** 实际使用的日志目录（首选不可写时会回退到 %TEMP% 或安装目录旁） */
  log_dir?: string
  /**
   * 日志目录是否真的可写 —— **日志为空时它是唯一的解释**（见 `logDisplayText`）。
   * 旧版本 Rust 不带这三个字段，故可选。
   */
  log_dir_writable?: boolean
  launcher_log_exists?: boolean
  backend_log_exists?: boolean
  exe_path: string
  /** TCP 能连上 7413 但 /health 不通 —— 通常是端口被别的程序占着 */
  port_occupied: boolean
  /** 安装目录可不可写（Rust 侧写探针文件判的） */
  install_dir_writable: boolean
}

/**
 * ⚠️ 这里**刻意没有**「是否致命 → 要不要自动放行」的判据函数（`isFatalStartupFailure`
 * 已于 2026-10-21 删除）：只要有诊断就停在失败屏，放行改由用户点「仍然继续」。
 * 那个函数当时的判据是 `alive`，而它正是「timeout 后自动放行、用户看不到任何原因」
 * 的来源 —— 判据存在一天，就有人会把它当成「什么时候能放行」的权威。
 */

/** 日志区文案 —— 没有输出时要说出来，而不是渲染一块空白让用户以为界面坏了。 */
export function logDisplayText(f: StartupFailure): string {
  const text = (f.log_tail || '').trim()
  if (text) return text

  // ⚠️ 「日志为空」有三种完全不同的原因，而用户看到的都是一块空白。
  // 区分它们靠 Rust 侧回传的三个事实（旧版本不带 → 退回最后的兜底文案）：
  //   ① 目录根本写不进去   → 日志系统自己失败了，这是**另一条**独立故障
  //   ② 可写但文件不存在   → 后端一行输出都没有（DLL/运行库缺失的典型形态）
  //   ③ 文件在但 tail 空   → 写了但没内容（极端情况：刚创建就被杀软清空）
  if (f.log_dir_writable === false) {
    const dir = f.log_dir || f.log_path
    return (
      `（日志目录写不进去：${dir}\n`
      + '这本身就是一条失败原因 —— 安全软件拦截、磁盘已满或用户环境变量异常都可能造成它。\n'
      + '这也解释了为什么启动器与后端两段日志同时为空。请把这条信息发给开发者。）'
    )
  }
  if (f.launcher_log_exists === false && f.backend_log_exists === false) {
    return (
      '（日志目录可写，但启动器与后端都没留下任何输出 —— 常见于：缺少 VC++ 运行库、'
      + 'DLL 缺失、被安全软件拦截，或安装不完整。可尝试重装到纯英文无空格的目录，'
      + '或查看安全软件的隔离记录。）'
    )
  }
  // 「两段日志都一行没有」是 DLL 缺失 / 运行库缺失 / 安全软件拦截的典型形态 ——
  // 这些故障在进程看来只是「退出」，只有这句话能把用户的下一个动作从
  // 「再点一次」指向「装运行库 / 查隔离记录 / 换个安装目录」。
  return (
    '（没有留下任何错误输出 —— 常见于：缺少 VC++ 运行库、安装路径含中文或特殊字符、'
    + '被安全软件拦截，或安装目录不完整）'
  )
}

/**
 * Windows NTSTATUS 退出码的解读。
 *
 * ⚠️ 这些故障在 **stdout/stderr 里一个字都没有**：进程在解释器起来之前就被
 * 加载器判死了，退出码是唯一的线索。而 `-1073741515` 对用户毫无意义 ——
 * 它对应的动作却是明确的一件事（装 VC++ 运行库）。翻译它就是在把
 * 「退出码」变成「下一步做什么」。
 */
const NTSTATUS_HINTS: Record<number, string> = {
  [-1073741515]: '0xC0000135 缺少 DLL —— 通常是没有安装 VC++ 运行库',
  [-1073741701]: '0xC000007B 程序与系统架构不匹配，或 DLL 版本冲突',
  [-1073741502]: '0xC0000142 DLL 初始化失败 —— 常见于缺少运行库或文件不完整',
  [-1073741819]: '0xC0000005 访问冲突 —— 可能被安全软件拦截',
  [-1073740791]: '0xC0000409 栈溢出保护触发 —— 程序自身异常退出',
  [-1073740777]: '0xC0000417 传给运行库的参数无效 —— 程序自身异常退出',
}

/** 退出码的人话解读；不认识（或没有退出码）时返回空串，调用方据此不渲染。 */
export function exitCodeHint(code: number | null | undefined): string {
  if (code === null || code === undefined) return ''
  return NTSTATUS_HINTS[code] || ''
}

/**
 * 安装路径兼容性提示（含空格 / 含非 ASCII / 不可写）。
 *
 * ⚠️ **不复用顶栏那条横幅的状态**：横幅是可被用户跨会话关掉的
 * （`isInstallWarningDismissed`），但这里出现的是**解释当前这次失败**的证据，
 * 不是「每次开机提醒一次」的重复噪音 —— 关过横幅不该让失败屏失去线索。
 */
export function installPathWarning(f: StartupFailure): string {
  return installWarningText({
    exe_path: f.exe_path,
    path_has_space: f.exe_path.includes(' '),
    path_has_non_ascii: hasNonAscii(f.exe_path),
    parent_writable: f.install_dir_writable,
    platform: 'windows',
  })
}

/** 路径里有没有非 ASCII 码点（中文用户名、全角符号…）。
 *  与 Rust 侧 `chars().any(|c| !c.is_ascii())` 同口径。 */
function hasNonAscii(s: string): boolean {
  for (const ch of s) {
    if (ch.codePointAt(0)! > 0x7f) return true
  }
  return false
}

/** 详情行：只列有信息的项，避免一排「无」占位置。 */
export function failureDetailLines(f: StartupFailure): string[] {
  const lines: string[] = []
  if (f.exit_code !== null && f.exit_code !== undefined) {
    const hint = exitCodeHint(f.exit_code)
    lines.push(hint ? `退出码：${f.exit_code}（${hint}）` : `退出码：${f.exit_code}`)
  }
  if (f.port_occupied) {
    lines.push('7413 端口已被其他程序占用')
  }
  lines.push(`后端程序：${f.exe_path}`)
  lines.push(`日志文件：${f.log_path}`)
  return lines
}

/**
 * 「复制诊断信息」的正文 —— 用户把这一整块发给开发者即可，不必自己在
 * 界面里框选（`<pre>` 里跨行选中在长日志上很难拖准，而漏掉开头那几行
 * 恰好是退出码和路径）。
 */
export function diagnosticsText(f: StartupFailure): string {
  const lines: string[] = [
    'LiMo 后端启动失败诊断',
    `结论：${f.summary}`,
    `阶段：${f.stage}`,
    `进程存活：${f.alive ? '是' : '否'}`,
    ...failureDetailLines(f),
  ]
  const warn = installPathWarning(f)
  if (warn) lines.push(`安装路径：${warn}`)
  lines.push('', '--- 日志（启动器 + 后端）---', logDisplayText(f))
  return lines.join('\n')
}
