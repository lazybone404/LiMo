import type { DocEntry } from '../api/core'

/**
 * LiMo — 「乐观插入」的文档条目（导入/合成完成的那一刻，先把这条塞进列表）。
 *
 * ## 为什么需要这个函数
 *
 * 导入完成后前端**不是**把后端返回的 `doc` 整体塞进 store，而是**手写一个字面量**调
 * `addDoc`（`GenPanel` 四处、`DocLibrary` 一处）。那份字面量在三个地方各写一遍之后，
 * 每加一个字段就漏几处，而漏掉的失效形态非常具体：
 *
 *   **刚导入的那一篇**字段是 `undefined`，切去文档库刷新一次列表反而正常 ——
 *   用户看到「刚导入不行、重启后可以」，开发看到「代码明明写了」。
 *
 * `modes` 当年就是这么踩的（见 `api/core.ts::DocEntry.modes` 的注释）；2026-11-07 加
 * `transcribeState` 时重演了一次风险，于是把那份字面量收成这一处。
 *
 * ⚠️ **加字段时只改这里**。不要再用 `addDoc({ id, title, ... })` 手写 —— 手写的那一刻
 * 就已经和这里分叉了，而 `tsc` 不会报错（`DocEntry` 的字段几乎全是可选的）。
 *
 * ## 为什么不是「后端返回什么就存什么」
 *
 * 后端 `done` 事件带的是 `_persist_ingest_result` 的返回（`ingest.py`），字段形状与
 * `DocEntry` **不完全相同**（少 `folderId` / `modes` / `tags` 等）。直接透传会让列表
 * 缺字段；一律走这里，语义就固定成「后端给的优先、缺的用兜底值」。
 */
export function optimisticDoc(
  doc: Partial<DocEntry> & { id: string; title: string },
  fallbackType: string,
): DocEntry {
  return {
    // ⚠️ `...doc` 必须摊在**最前**，下面逐个 `||` 兜底 —— 反过来的话，后端返回的
    // `null`（Python 的 `None` 经 JSON 就是 `null`，例如没有封面的网页）会盖掉兜底值，
    // 而 `DocEntry` 那侧声明的是 `string`。
    ...doc,
    id: doc.id,
    title: doc.title,
    // 后端给了就用后端的（视频/网页/文档各不同）；没给时用调用方所在 tab 的类型
    sourceType: doc.sourceType || fallbackType,
    sourceFileName: doc.sourceFileName || '',
    sourceUrl: doc.sourceUrl || '',
    coverUrl: doc.coverUrl || '',
    engine: doc.engine || '',
    language: doc.language || '',
    contentHash: doc.contentHash || '',
    tags: doc.tags || [],
    createdAt: doc.createdAt || new Date().toISOString(),
    folderId: doc.folderId || '',
    // ⚠️ **必须显式带**。漏了它就是「刚导入的这篇不显示待转录徽章、预览区不出待转录
    // 卡片、生成前置拦截也不触发」，而刷新一次列表（走 `listTranscripts`）后就全好了。
    // 缺键 = 已转录（与后端 `entry.get("transcribeState", "done")` 同一条兜底）。
    transcribeState: doc.transcribeState ?? 'done',
    // `folderId` 之外的字段（`modes` / `sourceDocIds` / `metadataCorrupt` …）由上面
    // 那次 `...doc` 原样带过来。`modes` 缺省保持 `undefined` = 产物情况未知，
    // 消费方会照旧发请求 —— **不要**在这里兜成 `[]`，那等于宣称「确定没有产物」。
  }
}
