/**
 * 「导入与转录分开」的前端三件事（2026-11-07）。
 *
 * 1. **两档偏好**（`lib/ingest-prefs.ts`）—— 默认值按成本分档，且**改完立刻对已挂载的
 *    GenPanel 生效**（`useSyncExternalStore`，不是挂载时读一次）。
 * 2. **参数只在偏离默认时才发**（`api/ingest.ts`）—— 少发一个键等于「与改造前逐字相同」。
 *    但两个参数的**后端默认值相反**（`media_probe_only=True` / `doc_probe_only=False`），
 *    所以「什么时候发」也相反：媒体档发 `false`、文档档发 `true`。
 * 3. **乐观条目带得上 `transcribeState`**（`lib/docEntry.ts`）—— 五处手写字面量收成一处的
 *    原因就在这里。
 *
 * 对照实验（每条都真跑过）：
 *   A. `INGEST_DEFAULTS.autoTranscribeDoc` 写成 `false` → 第一组红。后果是升级后
 *      导入网页也变成「只收纳」，而设置页文案仍写着「默认开启」。
 *   B. `loadIngestPrefs` 每次 `return parsePrefs(raw)` 不缓存 → 稳定引用那条红
 *      （真跑起来是 `useSyncExternalStore` 无限重渲染）。
 *   C. `optimisticDoc` 里漏掉 `transcribeState` → 最后一组红。后果是**刚导入的那一篇**
 *      不显示「待转录」徽章，切去文档库重新拉列表又正常。
 *   D. 两处都写成「真值才发」→ 第 4 组第一条红。而 `mediaProbeOnly=false` 正是用户
 *      打开「导入视频后自动转录」时的取值 —— 参数发不出去、后端用默认值 `True`，
 *      设置项完全失效而界面显示已打开，零报错。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'

import {
  INGEST_DEFAULTS,
  loadIngestPrefs,
  saveIngestPrefs,
  useIngestPrefs,
} from '../lib/ingest-prefs'
import { optimisticDoc } from '../lib/docEntry'
import { ingestURL } from '../api/ingest'

const KEY = 'mbw_ingestPrefs'

beforeEach(() => localStorage.clear())
afterEach(() => vi.unstubAllGlobals())

// ── 1. 默认值 ──

describe('默认值按成本分档', () => {
  it('媒体默认只收纳（false）/ 文档网页默认一键（true）', () => {
    // 对照 A：autoTranscribeDoc 改成 false → 这条红
    expect(INGEST_DEFAULTS).toEqual({ autoTranscribeMedia: false, autoTranscribeDoc: true })
  })

  it('无存储时返回默认', () => {
    expect(loadIngestPrefs()).toEqual(INGEST_DEFAULTS)
  })
})

// ── 2. 读写与容错 ──

describe('读写与容错', () => {
  it('保存是合并式：改一项不会重置另一项', () => {
    saveIngestPrefs({ autoTranscribeMedia: true })
    saveIngestPrefs({ autoTranscribeDoc: false })
    expect(loadIngestPrefs()).toEqual({ autoTranscribeMedia: true, autoTranscribeDoc: false })
  })

  it('损坏的 JSON → 全部落回默认（不抛）', () => {
    localStorage.setItem(KEY, '{不是 json')
    expect(loadIngestPrefs()).toEqual(INGEST_DEFAULTS)
  })

  it('缺键 / 非布尔 → 只退回该项默认，另一项保留', () => {
    localStorage.setItem(KEY, JSON.stringify({ autoTranscribeMedia: 'true', autoTranscribeDoc: false }))
    // `'true'` 是字符串，若直接收下 = 静默把一个坏值当有效值
    expect(loadIngestPrefs()).toEqual({ autoTranscribeMedia: false, autoTranscribeDoc: false })
  })

  it('磁盘未变时返回**同一个引用**（否则 useSyncExternalStore 无限重渲染）', () => {
    // 对照 B：每次都 parse 出新对象 → 这条红
    expect(loadIngestPrefs()).toBe(loadIngestPrefs())
  })

  it('磁盘变了就换引用（localStorage.clear / 手改 / 另一标签页）', () => {
    const before = loadIngestPrefs()
    localStorage.setItem(KEY, JSON.stringify({ autoTranscribeMedia: true, autoTranscribeDoc: true }))
    const after = loadIngestPrefs()
    expect(after).not.toBe(before)
    expect(after.autoTranscribeMedia).toBe(true)
  })
})

// ── 3. 订阅：设置页改完，已挂载的消费方立刻跟着变 ──

describe('useIngestPrefs 订阅', () => {
  it('已挂载的 hook 在 saveIngestPrefs 之后同步更新', () => {
    const { result } = renderHook(() => useIngestPrefs())
    expect(result.current.autoTranscribeMedia).toBe(false)
    act(() => saveIngestPrefs({ autoTranscribeMedia: true }))
    expect(result.current.autoTranscribeMedia).toBe(true)
  })
})

// ── 4. 请求参数：各按自己的后端默认值判方向 ──

function sseDoneResponse() {
  // 只提供 ingest.ts 真正取用的成员（headers.get / body.getReader）
  const payload = 'data: {"type":"done","id":"tr_1","title":"t","status":"done"}\n\n'
  return {
    ok: true,
    headers: { get: (k: string) => (k.toLowerCase() === 'content-type' ? 'text/event-stream' : null) },
    body: {
      getReader() {
        let sent = false
        return {
          async read() {
            if (sent) return { done: true, value: undefined }
            sent = true
            return { done: false, value: new TextEncoder().encode(payload) }
          },
        }
      },
    },
  } as unknown as Response
}

/** 发一次导入，返回 fetch 实际收到的 body 文本。 */
async function sentBody(fn: (opts: Parameters<typeof ingestURL>[5]) => void) {
  let bodyText = ''
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init: RequestInit) => {
    bodyText = String(init.body)
    return sseDoneResponse()
  }))
  const opts: Parameters<typeof ingestURL>[5] = {}
  fn(opts)
  await ingestURL('https://example.com/a', () => {}, () => {}, undefined, undefined, opts)
  return bodyText
}

describe('请求参数', () => {
  // 后端默认：media_probe_only=True / doc_probe_only=False。
  // 「等于默认就不发」⇒ 两档要发的值方向相反，这是本组每条断言的由来。

  it('省略 / 等于各自默认 → body 里一个开关都不出现', async () => {
    const omitted = await sentBody(() => {})
    expect(omitted).not.toContain('media_probe_only')
    expect(omitted).not.toContain('doc_probe_only')

    // mediaProbeOnly=true 就是后端默认值（只收纳），没有必要发
    const atDefault = await sentBody(o => { o!.mediaProbeOnly = true })
    expect(atDefault).not.toContain('media_probe_only')

    // docProbeOnly=false 就是后端默认值（一键提取），同样不发
    const docAtDefault = await sentBody(o => { o!.docProbeOnly = false })
    expect(docAtDefault).not.toContain('doc_probe_only')
  })

  it('mediaProbeOnly=false → 带上 media_probe_only=false（用户打开了自动转录）', async () => {
    // 对照 D：写成「真值才发」→ 这条红
    const body = await sentBody(o => { o!.mediaProbeOnly = false })
    expect(body).toContain('media_probe_only=false')
    expect(body).not.toContain('doc_probe_only')
  })

  it('docProbeOnly=true → 带上 doc_probe_only=true（用户关掉了自动提取）', async () => {
    const body = await sentBody(o => { o!.docProbeOnly = true })
    expect(body).toContain('doc_probe_only=true')
    expect(body).not.toContain('media_probe_only')
  })

  it('两个都偏离默认 → 两个键都在', async () => {
    const body = await sentBody(o => { o!.mediaProbeOnly = false; o!.docProbeOnly = true })
    expect(body).toContain('media_probe_only=false')
    expect(body).toContain('doc_probe_only=true')
  })

  it('文件导入（FormData）同样带得上', async () => {
    let bodyText = ''
    vi.stubGlobal('fetch', vi.fn(async (_url: string, init: RequestInit) => {
      bodyText = [...(init.body as FormData).entries()].map(([k, v]) => `${k}=${v}`).join('&')
      return sseDoneResponse()
    }))
    const { ingestFile } = await import('../api/ingest')
    await ingestFile(new File(['x'], 'a.txt'), () => {}, () => {}, undefined, undefined,
      { mediaProbeOnly: false, docProbeOnly: true })
    expect(bodyText).toContain('media_probe_only=false')
    expect(bodyText).toContain('doc_probe_only=true')
  })
})

// ── 5. 乐观条目 ──

describe('optimisticDoc', () => {
  it('**保留**后端给的 transcribeState: pending', () => {
    // 对照 C：函数里漏掉这个字段 → 刚导入的那篇不显示「待转录」
    const doc = optimisticDoc({ id: 'a', title: 't', transcribeState: 'pending' }, 'video')
    expect(doc.transcribeState).toBe('pending')
  })

  it('后端没给（历史 / 不支持的类型）→ done', () => {
    expect(optimisticDoc({ id: 'a', title: 't' }, 'text').transcribeState).toBe('done')
    // 显式 null（Python None 经 JSON）同样落 done
    expect(optimisticDoc({ id: 'a', title: 't', transcribeState: null as never }, 'text').transcribeState)
      .toBe('done')
  })

  it('后端给的 null 不会盖掉兜底值（`...doc` 摊在最前的意义）', () => {
    const doc = optimisticDoc(
      { id: 'a', title: 't', sourceUrl: null as never, tags: null as never },
      'webpage',
    )
    expect(doc.sourceUrl).toBe('')
    expect(doc.tags).toEqual([])
  })

  it('modes 缺省保持 undefined（= 产物情况未知，消费方会照旧发请求）', () => {
    expect(optimisticDoc({ id: 'a', title: 't' }, 'video').modes).toBeUndefined()
  })
})
