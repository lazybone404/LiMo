/**
 * format — 通用格式化工具。
 *
 * formatSize：按字节智能选单位（B/KB/MB/GB），替代散落在组件里的固定 KB/MB 显示。
 * 参考文件 / 导入文件大小显示统一走这里，避免 5MB 显示成 5120 KB 的割裂感。
 */
export function formatSize(bytes: number): string {
  if (!bytes || bytes < 0) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  const kb = bytes / 1024
  if (kb < 1024) return `${kb.toFixed(kb < 10 ? 1 : 0)} KB`
  const mb = kb / 1024
  if (mb < 1024) return `${mb.toFixed(mb < 10 ? 1 : 0)} MB`
  const gb = mb / 1024
  return `${gb.toFixed(gb < 10 ? 1 : 0)} GB`
}
