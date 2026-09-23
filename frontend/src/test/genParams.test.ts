/**
 * 生成请求的参数序列化 —— 只测「新参数有没有真的进 URL」这一件事。
 *
 * 为什么单独立一个文件：模式 #6 在这个仓库已经反复踩过 5 次（`dedupe_enabled` /
 * `unit_width` / `unit_height` / `save_quality` / `frame_quality_filter` 都曾因为
 * `streamGenerate` 里漏了 `params.set()` 而从未生效）。它的失败方式极其安静：
 * 前端一切正常、后端收到默认值、功能「上线了但没反应」，日志里什么都看不到。
 *
 * 故这里对 `reasoning_effort` 补上断链检查：**在 URL 里断言**，而不是断言
 * 「传了 options」—— 后者在漏 set 的实现下照样通过。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { streamGenerate, type StreamGenerateCallbacks } from '../api/generate'

const calls: string[] = []

function cb(): StreamGenerateCallbacks {
  return {
    onChunk: () => {}, onDone: () => {}, onError: () => {}, onCancelled: () => {},
  }
}

/** 只关心 URL：body 为 null 会让 streamGenerate 走 onError，但 URL 已记录 */
function stubFetch() {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    calls.push(String(url))
    return { ok: true, body: null } as unknown as Response
  }))
}

function queryOf(i = 0): URLSearchParams {
  return new URL(calls[i], 'http://localhost').searchParams
}

beforeEach(() => { calls.length = 0; stubFetch() })
afterEach(() => { vi.unstubAllGlobals() })

describe('streamGenerate — reasoning_effort 序列化', () => {
  it('传了档位就必须出现在 URL 里', () => {
    streamGenerate('doc1', { mode: 'notes', reasoning_effort: 'low' }, cb())
    // ⚠️ 对照（已验证有区分力）：注释掉 api/generate.ts 里的 params.set 行 → 本条变红。
    expect(queryOf().get('reasoning_effort')).toBe('low')
  })

  it('三个档位都能原样传达（含 max —— 它是唯一会改变整理版档位的值）', () => {
    const tiers: Array<'low' | 'high' | 'max'> = ['low', 'high', 'max']
    for (const tier of tiers) {
      calls.length = 0
      streamGenerate('doc1', { mode: 'notes', reasoning_effort: tier }, cb())
      expect(queryOf().get('reasoning_effort')).toBe(tier)
    }
  })

  it('未指定时不带该参数（后端据此走服务端默认，而非收到一个空串档位）', () => {
    streamGenerate('doc1', { mode: 'notes' }, cb())
    expect(queryOf().has('reasoning_effort')).toBe(false)
  })

  it('显式 undefined 与不传等价（前端对不支持的模型就是这样传的）', () => {
    streamGenerate('doc1', { mode: 'notes', reasoning_effort: undefined }, cb())
    expect(queryOf().has('reasoning_effort')).toBe(false)
  })
})

describe('streamGenerate — doc_direct 序列化', () => {
  it('开启时出现在 URL 里', () => {
    streamGenerate('doc1', { mode: 'notes', doc_direct: true }, cb())
    // ⚠️ 对照（验证有区分力）：注释掉 api/generate.ts 里的 params.set 行 → 本条变红。
    expect(queryOf().get('doc_direct')).toBe('true')
  })

  /**
   * 这条是 doc_direct 与 reasoning_effort 的**语义分水岭**，也是模式 #6 最容易
   * 踩错的一格：
   *   - reasoning_effort：「不传」= 服务端默认（high），故 false/undefined 都该省掉
   *   - doc_direct：「不传」= 文本模式，而**用户关掉直传的意图必须传得出去**
   *     （开着 → 关掉，若用 truthiness 判断，false 被跳过，后端仍收到上次的 true）
   * 也就是说这里断言的不是「true 能传」，而是「**false 也能传**」。
   */
  it('显式 false 必须照样出现在 URL（用户「关掉直传」的意图不能被吞）', () => {
    streamGenerate('doc1', { mode: 'notes', doc_direct: false }, cb())
    expect(queryOf().get('doc_direct')).toBe('false')
  })

  it('未指定时不带该参数（后端走默认文本模式）', () => {
    streamGenerate('doc1', { mode: 'notes' }, cb())
    expect(queryOf().has('doc_direct')).toBe(false)
  })
})
