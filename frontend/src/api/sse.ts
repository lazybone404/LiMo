/**
 * 公共 SSE 流解析器 — 消除 6 处重复的 SSE 解析逻辑。
 *
 * 用法：
 *   const reader = response.body?.getReader()
 *   await parseSSEStream(reader, (data) => {
 *     // 分发事件：switch (data.type) ...
 *   })
 */

/** SSE 事件回调 — 参数来自 JSON.parse，类型为 any */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type SSEEventHandler = (data: any) => void

/**
 * 读 SSE 流并逐行解析 JSONL 事件。
 * 支持 "data: {...}" 单行格式（兼容标准 SSE 的 \n\n 分隔）。
 */
export async function parseSSEStream(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  onEvent: SSEEventHandler,
): Promise<void> {
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    // 归一化 CRLF → LF（部分 LLM API 返回 CRLF）
    buffer = buffer.replace(/\r\n/g, '\n')
    const lines = buffer.split('\n')
    // 最后一行可能不完整，保留到下次循环
    buffer = lines.pop() || ''

    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      try {
        const data = JSON.parse(line.slice(6))
        onEvent(data)
      } catch { /* 跳过损坏的 JSON */ }
    }
  }
}

