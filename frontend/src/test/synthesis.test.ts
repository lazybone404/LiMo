/**
 * 合成请求（POST + SSE）—— 只测「请求有没有把选中集原样带出去」与事件分发。
 *
 * 这是全项目唯一的 **POST + body SSE 端点**，所以两件事都容易错、且都错得很安静：
 * ① 照抄别处的 GET + query 写法 → 后端 422，前端只看到「请求失败」；
 * ② body 字段名写错一个（`docIds` vs `doc_ids`）→ 后端收到空列表 →
 *    用户看到的是「请至少选择 2 篇」而不是「参数错误」——**症状指向完全错误的方向**。
 * 故这里断言的是**发出去的 body 本身**，不是「传了 options」。
 *
 * 对照实验（均已实测，证明本文件有区分力）：
 *   A. `doc_ids: sourceDocIds` → `doc_ids: []`
 *      → 红 2 条（`doc_ids` 与全字段相等两条）
 *   B. `method: 'POST'` → `'GET'`（顺手删掉 Content-Type）
 *      → 红 1 条（「用 POST + JSON」）
 *   C. 删掉 `if (!receivedDoneOrError)` 兜底
 *      → 红 1 条（「连接意外断开」）——这一条是**唯一**能发现该兜底被删的用例，
 *        因为其余用例都会先收到 done/error。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { streamSynthesis, type StreamSynthesisCallbacks, type SynthesisOptions } from '../api/synthesis'

interface Captured {
  url: string
  init: RequestInit
}

const captured: Captured[] = []
const results: Array<{ type: string; args: unknown[] }> = []

function cb(): StreamSynthesisCallbacks {
  return {
    onStage: (m) => results.push({ type: 'stage', args: [m] }),
    onDone: (id, mode, degraded, excluded) =>
      results.push({ type: 'done', args: [id, mode, degraded, excluded] }),
    onError: (code, message, cancelled) => results.push({ type: 'error', args: [code, message, cancelled] }),
  }
}

/** 造一个把给定 SSE 行逐块吐出来的假响应 */
function sseResponse(lines: string[]): Response {
  const chunks = lines.map((l) => new TextEncoder().encode(l))
  let i = 0
  return {
    ok: true,
    status: 200,
    body: {
      getReader: () => ({
        read: async () =>
          i < chunks.length ? { done: false, value: chunks[i++] } : { done: true, value: undefined },
        cancel: async () => {},
      }),
    },
  } as unknown as Response
}

function stubFetch(resp: Response | (() => Response)) {
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
    captured.push({ url: String(url), init })
    return typeof resp === 'function' ? resp() : resp
  }))
}

function bodyOf(i = 0): Record<string, unknown> {
  return JSON.parse(String(captured[i].init.body))
}

beforeEach(() => { captured.length = 0; results.length = 0 })
afterEach(() => { vi.unstubAllGlobals() })

describe('streamSynthesis — 请求形状', () => {
  it('选中集逐项进 body.doc_ids（不是 [currentDocId]、不是空数组）', async () => {
    stubFetch(sseResponse(['data: {"type":"done","id":"tr_new","mode":"stuff"}\n\n']))
    streamSynthesis(['tr_a', 'tr_b', 'tr_c'], {}, cb())
    await vi.waitFor(() => expect(results.length).toBe(1))
    expect(bodyOf().doc_ids).toEqual(['tr_a', 'tr_b', 'tr_c'])
  })

  it('用 POST + JSON（照抄 GET + query 的写法会 422）', async () => {
    stubFetch(sseResponse(['data: {"type":"done","id":"x"}\n\n']))
    streamSynthesis(['tr_a', 'tr_b'], {}, cb())
    await vi.waitFor(() => expect(results.length).toBe(1))
    expect(captured[0].init.method).toBe('POST')
    expect((captured[0].init.headers as Record<string, string>)['Content-Type']).toBe('application/json')
    expect(captured[0].url).toContain('/synthesis/stream')
    expect(captured[0].url).not.toContain('?')  // 参数一律走 body
  })

  it('title / user_notes / model_id 原样带出', async () => {
    stubFetch(sseResponse(['data: {"type":"done","id":"x"}\n\n']))
    streamSynthesis(['tr_a', 'tr_b'],
      { title: '甲乙对比', userNotes: '重点看方法差异', modelId: 7 }, cb())
    await vi.waitFor(() => expect(results.length).toBe(1))
    expect(bodyOf()).toMatchObject({
      title: '甲乙对比', user_notes: '重点看方法差异', model_id: 7,
    })
  })

  it('未给的字段填空值而不是缺字段（后端 pydantic 有默认值，但缺字段会让类型漂移）', async () => {
    stubFetch(sseResponse(['data: {"type":"done","id":"x"}\n\n']))
    streamSynthesis(['tr_a', 'tr_b'], {}, cb())
    await vi.waitFor(() => expect(results.length).toBe(1))
    expect(bodyOf()).toEqual({
      doc_ids: ['tr_a', 'tr_b'], title: '', user_notes: '', model_id: null,
      dimensions: [], source_mode: 'raw',
    })
  })

  it('dimensions / source_mode 原样带出（维度是产物骨架，原料模式决定读哪份原料）', async () => {
    stubFetch(sseResponse(['data: {"type":"done","id":"x"}\n\n']))
    streamSynthesis(['tr_a', 'tr_b'],
      { dimensions: ['研究问题', '方法'], sourceMode: 'notes' }, cb())
    await vi.waitFor(() => expect(results.length).toBe(1))
    expect(bodyOf()).toMatchObject({
      dimensions: ['研究问题', '方法'], source_mode: 'notes',
    })
  })
})

describe('streamSynthesis — 事件分发', () => {
  it('stage → onStage（长任务的「还活着」信号）', async () => {
    stubFetch(sseResponse([
      'data: {"type":"stage","message":"读取源文档…"}\n\n',
      'data: {"type":"stage","message":"逐篇结构化抽取（1/3）…"}\n\n',
      'data: {"type":"done","id":"tr_new","mode":"map_reduce"}\n\n',
    ]))
    streamSynthesis(['a', 'b'], {}, cb())
    await vi.waitFor(() => expect(results.some((r) => r.type === 'done')).toBe(true))
    const stages = results.filter((r) => r.type === 'stage').map((r) => r.args[0])
    // ⚠️ 顺序也要对：用户看到的是「读取源文档」先于「逐篇抽取」
    expect(stages).toEqual(['读取源文档…', '逐篇结构化抽取（1/3）…'])
  })

  it('done → onDone(id, mode, degraded, excluded)；两个数组缺省补空', async () => {
    stubFetch(sseResponse([
      'data: {"type":"done","id":"tr_new","mode":"map_reduce","degraded":["论文丙"],"excluded":["无笔记篇"]}\n\n',
    ]))
    streamSynthesis(['a', 'b'], {}, cb())
    await vi.waitFor(() => expect(results.length).toBe(1))
    expect(results[0]).toEqual({
      type: 'done', args: ['tr_new', 'map_reduce', ['论文丙'], ['无笔记篇']],
    })

    results.length = 0; captured.length = 0
    stubFetch(sseResponse(['data: {"type":"done","id":"tr_x","mode":"stuff"}\n\n']))
    streamSynthesis(['a', 'b'], {}, cb())
    await vi.waitFor(() => expect(results.length).toBe(1))
    expect(results[0].args).toEqual(['tr_x', 'stuff', [], []])
  })

  it('业务失败 → onError(code, message, cancelled=false)', async () => {
    stubFetch(sseResponse([
      'data: {"type":"error","code":"SYNTH_WINDOW_TOO_SMALL","message":"当前模型窗口不足以做多文档综述"}\n\n',
    ]))
    streamSynthesis(['a', 'b'], {}, cb())
    await vi.waitFor(() => expect(results.length).toBe(1))
    expect(results[0]).toEqual({
      type: 'error', args: ['SYNTH_WINDOW_TOO_SMALL', '当前模型窗口不足以做多文档综述', false],
    })
  })

  it('用户取消（SYNTH_CANCELLED）→ cancelled=true，供 UI 不弹错误提示', async () => {
    stubFetch(sseResponse(['data: {"type":"error","code":"SYNTH_CANCELLED","message":"合成已取消"}\n\n']))
    streamSynthesis(['a', 'b'], {}, cb())
    await vi.waitFor(() => expect(results.length).toBe(1))
    expect(results[0].args[2]).toBe(true)
  })

  it('流正常结束却没收到 done/error → 兜底报「连接意外断开」（否则 UI 永久停在合成中）', async () => {
    stubFetch(sseResponse(['data: {"type":"stage","message":"读取源文档…"}\n\n']))
    streamSynthesis(['a', 'b'], {}, cb())
    await vi.waitFor(() => expect(results.some((r) => r.type === 'error')).toBe(true))
    expect(results.find((r) => r.type === 'error')!.args[0]).toBe('SYNTH_DISCONNECTED')
  })

  it('非 2xx → onError 带上后端错误码（不进 SSE 分支）', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: false, status: 500,
      json: async () => ({ code: 'SYNTH_FAILED', message: '合成失败: boom' }),
    }) as unknown as Response))
    streamSynthesis(['a', 'b'], {}, cb())
    await vi.waitFor(() => expect(results.length).toBe(1))
    expect(results[0].args).toEqual(['SYNTH_FAILED', '合成失败: boom', false])
  })
})

// 供 TS 引用，避免 `SynthesisOptions` 未使用告警
export type _Opts = SynthesisOptions
