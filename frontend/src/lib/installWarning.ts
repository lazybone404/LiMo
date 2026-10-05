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
