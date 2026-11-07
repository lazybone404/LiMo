/**
 * 批量导入的文件识别（2026-11-07）。
 *
 * 一次拖入多个文件时，每个文件按扩展名判断该走哪条导入通道。判断结果只有四种：
 * 视频 / 图片 / 文档 / 文本。前三种分别对应 `ingestFile` 与视频、图片、文档三条
 * 既有的 adapter 路由；文本类（`.md` / `.txt`）读成字符串走 `ingestText`，
 * 与文本标签页「上传 MD · TXT 文件」是同一件事。
 *
 * ⚠️ 判定表复用 `constants.ts` 的 `DROP_ACCEPT` 与 `EXT_TO_SUBTYPE`，不在这里
 * 另抄一份扩展名清单。抄一份的后果是两处慢慢分叉：单文件拖拽区接受 `.webm`，
 * 批量识别却说「不支持」，而两条路径都不报错。
 *
 * ⚠️ 这里只做**识别**，不发起任何请求。真正开始导入要等用户在对话框里点确认
 * （`useBatchImport` 的 `confirm`）。
 */
import { DROP_ACCEPT, EXT_TO_SUBTYPE } from './constants'

export type BatchKind = 'video' | 'image' | 'file' | 'text'

/** 一次批量导入最多接受的文件数。超出的部分不生成条目，由对话框提示截断。 */
export const BATCH_MAX_FILES = 50

/** 后端上传硬上限（`_handle_file_upload` 的 2GB 预检）。超过的文件直接标为不可导入。 */
export const MAX_FILE_BYTES = 2 * 1024 * 1024 * 1024

/** 超过这个体积只在导入前提示一次，不拦截。 */
export const WARN_FILE_BYTES = 1 * 1024 * 1024 * 1024

/** 文本标签页的上传格式。与 `ImportSection` 里那个 `<input accept>` 同一组。 */
const TEXT_EXTS = ['.md', '.markdown', '.txt', '.rst']

export interface ClassifiedFile {
  kind: BatchKind
  /** 文档子类型（pdf / word / ppt / excel / epub）。非文档类为空串。 */
  subType: string
}

/**
 * 一个待导入的文件。
 *
 * 不写成 `extends ClassifiedFile`：认不出来的文件也要留在清单里给用户看原因，
 * 而那种条目没有类别。继承会让 `kind` 的类型被迫收窄成非空，与这个事实矛盾。
 */
export interface BatchCandidate {
  file: File
  /** 识别出的类别。认不出来时为 `null`。 */
  kind: BatchKind | null
  /** 文档子类型（pdf / word / ppt / excel / epub）。非文档类为空串。 */
  subType: string
  /** 不能导入的原因。空串表示可以导入。 */
  reject: string
}

function endsWithAny(name: string, exts: string[] | undefined): boolean {
  if (!exts) return false
  return exts.some(ext => name.endsWith(ext))
}

/**
 * 按扩展名识别一个文件。认不出来返回 `null`，调用方把它标成「不支持的文件类型」。
 *
 * 顺序按 `DROP_ACCEPT` 的三张表依次试：视频、图片、文档。三张表没有重叠的扩展名，
 * 所以顺序不影响结果，写成固定顺序只是为了结果稳定可测。
 */
export function classifyFile(file: File): ClassifiedFile | null {
  const name = file.name.toLowerCase()
  if (endsWithAny(name, DROP_ACCEPT.video)) return { kind: 'video', subType: '' }
  if (endsWithAny(name, DROP_ACCEPT.image)) return { kind: 'image', subType: '' }
  if (endsWithAny(name, DROP_ACCEPT.file)) {
    const dot = name.lastIndexOf('.')
    return { kind: 'file', subType: EXT_TO_SUBTYPE[name.slice(dot)] || '' }
  }
  if (endsWithAny(name, TEXT_EXTS)) return { kind: 'text', subType: '' }
  return null
}

/**
 * 把拖入的文件列表转成对话框的条目。
 *
 * 两种不可导入的情况在识别阶段就定下来，不再进入队列：扩展名不认识、超过 2GB。
 * 把它们标在条目上而不是直接丢掉，是为了让用户在确认之前就看到「这个文件不会被导入」——
 * 静默丢弃会让用户以为漏选了。
 */
export function buildBatchCandidates(files: File[]): BatchCandidate[] {
  return files.map((file) => {
    const hit = classifyFile(file)
    if (!hit) return { file, kind: null, subType: '', reject: '不支持的文件类型' }
    if (file.size > MAX_FILE_BYTES) return { ...hit, file, reject: '超过 2GB 上传上限' }
    return { ...hit, file, reject: '' }
  })
}

/**
 * 截断到上限并识别。返回值里的 `truncated` 是没进清单的文件数，由对话框提示。
 *
 * 截断放在识别之前：超出的文件连条目都不生成，避免用户看到一屏「其实不会导入」的行。
 */
export function planBatchFiles(files: File[]): { candidates: BatchCandidate[]; truncated: number } {
  const accepted = files.slice(0, BATCH_MAX_FILES)
  return { candidates: buildBatchCandidates(accepted), truncated: files.length - accepted.length }
}
