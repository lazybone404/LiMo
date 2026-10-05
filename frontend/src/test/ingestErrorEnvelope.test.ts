/**
 * 导入错误信封（`P0-05-ingest-error-envelope`）。
 *
 * 后端统一迁到 AppError 后，业务错误返回的是 `{code, message, detail, recoverable}`，
 * **没有 `error` 字段**。而 `api/ingest.ts` 两处判据只写了 `data.error` —— 于是新信封的
 * 业务错误**一个都不命中**，被当作成功送给 `onDone`：文档根本没落库，界面上却没有任何
 * 报错（`generate.ts` / `transcripts.ts` 早就改成了 `data.error || data.code`）。
 *
 * ⚠️ 判据**不能图省事写成 `data.error || data.message`**，本文件第 3 组用例就是钉这件事：
 * 「已导入过《…》」「内容与已有…完全相同」是**去重成功**，后端响应里本来就带 `message`
 * （见 `routers/ingest.py` 的 duplicate_url / duplicate_content 分支）。拿 message 判据
 * 会把正常去重报成失败 —— 那是把一个静默失效换成另一个静默失效。
 *
 * ⚠️ 两处**都要**覆盖（主路径 + `fallbackJson`）：后者只在流式失败时才走，只测主路径的
 * 实现可以让「SSE 正常时能报错、降级后不能报错」全绿通过。
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { ingestText } from '../api/ingest'

/** 伪响应：只提供 ingest.ts 真正取用的两个成员（headers.get / json）。 */
function jsonResponse(body: unknown, contentType = 'application/json') {
  return {
    ok: true,
    body: null,
    headers: { get: (k: string) => (k.toLowerCase() === 'content-type' ? contentType : null) },
    json: async () => body,
  }
}

function stubOnce(impl: () => unknown | Promise<unknown>) {
  vi.stubGlobal('fetch', vi.fn(async () => await impl()))
}

/** 跑一次导入，返回三个回调的调用记录。 */
async function run() {
  const calls = { done: [] as any[], err: [] as string[], progress: [] as any[] }
  await ingestText('测试文本', (p) => calls.progress.push(p),
    (d) => calls.done.push(d), (e) => calls.err.push(e))
  return calls
}

afterEach(() => vi.unstubAllGlobals())

// ── 1. 新信封 `{code, message}`（AppError）必须报错 ──

describe('新 AppError 信封', () => {
  it('主路径：{code,message} → onError 且不进 onDone', async () => {
    stubOnce(() => jsonResponse({ code: 'INGEST_ADAPTER_FAILED', message: '视频下载失败' }))
    const { done, err } = await run()
    expect(err).toEqual(['视频下载失败'])
    expect(done).toHaveLength(0)   // ← 缺陷原貌：这条曾是 1（被当成功入库）
  })

  it('fallbackJson：流式失败后同样认新信封', async () => {
    let n = 0
    vi.stubGlobal('fetch', vi.fn(async () => {
      n++
      if (n === 1) throw new Error('stream endpoint unreachable')
      return jsonResponse({ code: 'INGEST_ADAPTER_FAILED', message: '视频下载失败' })
    }))
    const { done, err } = await run()
    expect(n).toBe(2)              // 前提：真的走了回退（只 fetch 一次的实现会红）
    expect(err).toEqual(['视频下载失败'])
    expect(done).toHaveLength(0)
  })

  it('没有 message 时退回 code（不产出 undefined 文案）', async () => {
    stubOnce(() => jsonResponse({ code: 'GEN_NO_API_KEY' }))
    const { err } = await run()
    expect(err).toEqual(['GEN_NO_API_KEY'])
  })
})

// ── 2. 旧信封 `{error}` 不能被改回去时砍掉 ──

describe('旧 error 信封', () => {
  it('{error: "..."} → onError 且不进 onDone', async () => {
    stubOnce(() => jsonResponse({ error: '旧的错误文案' }))
    const { done, err } = await run()
    expect(err).toEqual(['旧的错误文案'])
    expect(done).toHaveLength(0)
  })
})

// ── 3. 反面：**成功**响应带着 message，绝不能被判成失败 ──
//
// 这条是「修 P0-05 时的过度修复」探测器：把判据写成 `data.error || data.message`
// 的实现，会在这里红。

describe('带 message 的成功响应', () => {
  it('duplicate_url（去重成功）→ 进 onDone，不报错', async () => {
    stubOnce(() => jsonResponse({
      id: 'tx_1', title: '已有文档', status: 'duplicate_url', message: '已导入过《已有文档》',
    }))
    const { done, err } = await run()
    expect(err).toHaveLength(0)
    expect(done).toEqual([{ id: 'tx_1', title: '已有文档', status: 'duplicate_url', message: '已导入过《已有文档》' }])
  })

  it('duplicate_content（去重成功）→ 进 onDone，不报错', async () => {
    stubOnce(() => jsonResponse({
      id: 'tx_2', title: '已有文档', status: 'duplicate_content', message: '内容与已有《已有文档》完全相同',
    }))
    const { done, err } = await run()
    expect(err).toHaveLength(0)
    expect(done).toHaveLength(1)
  })

  it('普通成功（无 code 无 error）→ 进 onDone', async () => {
    stubOnce(() => jsonResponse({ status: 'done', id: 'tx_3', title: '新文档' }))
    const { done, err } = await run()
    expect(err).toHaveLength(0)
    expect(done[0].id).toBe('tx_3')
  })
})
