/**
 * 「这篇文档要不要发产物请求」的**唯一判据**（`DocEntry.modes` 的消费端）。
 *
 * 背景：`modes` 由列表端点随 `index.json` 一起带出，前端据此决定发不发
 * `GET /api/markdown/{id}?mode=…`。在此之前无论有没有产物都盲发，没生成过就是 404
 * ——前端虽然静默掉了（不进 console.error、不弹 toast），但**浏览器网络层照打红字**
 * （WebView 里 JS 抑制不掉，模式 #49），而且协议层把「尚未生成」这个**正常状态**
 * 建模成了 HTTP 错误。
 *
 * ⚠️ **`undefined` 与 `[]` 必须分流，这是本模块存在的全部理由**：
 *   - `undefined` = **未知**（旧后端 / 测试 mock / 乐观插入的文档）→ 照旧发请求
 *   - `[]`        = **确定没有产物** → 一个请求都不发
 * 写成 `modes?.includes('notes') ?? true` 之类的简写会把两者混为一谈：未知时**不发
 * 请求**，于是旧后端下所有文档的笔记、整理版**全部不可见**，且零报错。
 *
 * 抽成纯函数是为了让这条分流**可断言**（渲染 `PreviewArea` 要 mock 掉四个 API，
 * 判据本身反而测不到）——与 `entryDelay` / `docMeta` / `filters` 同一做法。
 */

export interface ArtifactRequestPlan {
  notes: boolean
  polished: boolean
}

export function planArtifactRequests(modes: string[] | undefined): ArtifactRequestPlan {
  // ⚠️ `undefined` 必须走 true 分支（见文件头）。不要改成 `modes?.includes(m) ?? …`——
  // 那会把「不知道」当成「没有」。
  return {
    notes: modes === undefined || modes.includes('notes'),
    polished: modes === undefined || modes.includes('polished'),
  }
}
