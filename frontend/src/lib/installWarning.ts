/**
 * 安装路径告警文案 —— 由 Rust `backend-warning` 事件的 payload 组装。
 *
 * ⚠️ 这个事件自落地起**前端零监听**（Rust 侧一直在发，`lib.rs::analyze_install_path`
 * 是认真写的：探空格、探非 ASCII、往父目录写一个探针文件看可不可写）。于是一条
 * 「装在含空格 / 中文 / 不可写的目录，后端可能根本起不来」的告警**永远不显示**，
 * 用户遇到启动失败时毫无线索。
 *
 * 接上它比删掉它正确（模式 #53：用户可见的信息要么能驱动行动，要么别出现 ——
 * 这一条**能**：换个安装目录）。抽成纯函数是为了可测：真实事件只在打包版、且
 * 路径真的有问题时才发一次，测试够不到。
 */
export interface InstallPathWarning {
  exe_path: string
  path_has_non_ascii: boolean
  path_has_space: boolean
  parent_writable: boolean
  platform: string
}

/** 无问题时返回空串 —— 调用方据此决定不渲染横幅（而不是渲染一条空告警）。 */
export function installWarningText(w: InstallPathWarning): string {
  const issues: string[] = []
  if (w.path_has_space) issues.push('路径含空格')
  if (w.path_has_non_ascii) issues.push('路径含非 ASCII 字符（如中文）')
  if (!w.parent_writable) issues.push('安装目录不可写')
  if (issues.length === 0) return ''
  return `安装位置可能有兼容问题：${issues.join('、')}。`
    + '建议重装到纯英文、无空格且可写的目录，否则后端可能无法启动或功能异常。'
}

/* ── 「用户主动关掉过」的记忆（2026-10-18） ────────────────────────────
   ⚠️ **跨会话**地记（localStorage），不是只压住本次渲染：这条告警在**每次启动**
   都会重新发（Rust `analyze_install_path` 每次跑），只清本地 state 的话用户每次
   开机都要再关一次 —— 而它对用户的实际答复只有两种：「我去重装」或「我知道，
   不打算改」。后者一旦表达过，再弹就是纯噪音。
   ⚠️ 噪音的代价不是「多看一眼」，是**对真正故障提示的习惯性忽略** —— 同一条横幅
   （顶栏）也承担后端崩溃告警，训练用户无脑关掉它是有代价的。
   ⚠️ 只记一个布尔，**不记路径、不记具体问题**：判断权在用户（他可能换了安装目录
   后遇到同一条），替他把「换目录 = 新问题」重新打开，等于用我们的猜测覆盖他的决定。
   ⚠️ 存储不可用（隐私模式 / 无 localStorage）时**退化为每次都提示** —— 记忆增强
   而不是渲染前提，读失败不能顺带把告警也一起吞掉。 */
const DISMISS_KEY = 'limo_install_warning_dismissed'

/** 用户是否主动关掉过这条告警（关过 = 本机不再提示）。 */
export function isInstallWarningDismissed(): boolean {
  try { return localStorage.getItem(DISMISS_KEY) === '1' } catch { return false }
}

/** 用户点了关闭 —— 记下来，之后每次启动都不再显示。 */
export function dismissInstallWarning(): void {
  try { localStorage.setItem(DISMISS_KEY, '1') } catch { /* 记不住就退化为每次都提示 */ }
}
