/**
 * 批量导入的文件识别（2026-11-07）。
 *
 * 这一层只做判定，不发请求，所以测试也只需要造 `File` 对象。真正发请求的串行队列
 * （`useBatchImport`）依赖 store 与 SSE，留给手工验收。
 *
 * ⚠️ 第一组测试是**同源守卫**：识别表直接从 `DROP_ACCEPT` 取，遍历它自己的每一项。
 * 手写一份扩展名清单的话，两处会慢慢分叉 —— 单文件拖拽区接受 `.webm`，批量识别却说
 * 「不支持」，而两条路径都不报错。这个守卫会在分叉的那一刻变红。
 */
import { describe, it, expect } from 'vitest'

import {
  BATCH_MAX_FILES,
  MAX_FILE_BYTES,
  buildBatchCandidates,
  classifyFile,
  planBatchFiles,
} from '../components/gen-panel/batchImport'
import { DROP_ACCEPT } from '../components/gen-panel/constants'

function makeFile(name: string, size = 8): File {
  const f = new File(['xxxxxxxx'], name)
  // jsdom 不允许真的分配 >2GB，用 defineProperty 改 size 去行使上限分支。
  if (size !== f.size) Object.defineProperty(f, 'size', { value: size })
  return f
}

describe('识别表与 DROP_ACCEPT 同源', () => {
  it('DROP_ACCEPT 里的每个视频扩展名都被识别成 video', () => {
    for (const ext of DROP_ACCEPT.video!) {
      expect(classifyFile(makeFile(`a${ext}`))?.kind, ext).toBe('video')
    }
  })

  it('DROP_ACCEPT 里的每个图片扩展名都被识别成 image', () => {
    for (const ext of DROP_ACCEPT.image!) {
      expect(classifyFile(makeFile(`a${ext}`))?.kind, ext).toBe('image')
    }
  })

  it('DROP_ACCEPT 里的每个文档扩展名都被识别成 file，且子类型非空', () => {
    for (const ext of DROP_ACCEPT.file!) {
      const hit = classifyFile(makeFile(`a${ext}`))
      expect(hit?.kind, ext).toBe('file')
      expect(hit?.subType, ext).not.toBe('')
    }
  })
})

describe('classifyFile', () => {
  it('文档按扩展名给出子类型', () => {
    expect(classifyFile(makeFile('论文.PDF'))).toEqual({ kind: 'file', subType: 'pdf' })
    expect(classifyFile(makeFile('a.docx'))).toEqual({ kind: 'file', subType: 'word' })
    expect(classifyFile(makeFile('a.pptx'))?.subType).toBe('ppt')
    expect(classifyFile(makeFile('a.xlsx'))?.subType).toBe('excel')
    expect(classifyFile(makeFile('a.epub'))?.subType).toBe('epub')
  })

  it('文本类走 text 通道', () => {
    for (const name of ['笔记.md', 'a.markdown', 'a.txt', 'a.rst']) {
      expect(classifyFile(makeFile(name))?.kind, name).toBe('text')
    }
  })

  it('扩展名大小写不影响判定', () => {
    expect(classifyFile(makeFile('A.MP4'))?.kind).toBe('video')
    expect(classifyFile(makeFile('B.PNG'))?.kind).toBe('image')
  })

  it('认不出来的扩展名返回 null', () => {
    expect(classifyFile(makeFile('a.xyz'))).toBeNull()
    expect(classifyFile(makeFile('没有扩展名'))).toBeNull()
  })
})

describe('buildBatchCandidates', () => {
  it('不可导入的原因标在条目上而不是丢掉文件', () => {
    const list = buildBatchCandidates([makeFile('a.xyz')])
    expect(list).toHaveLength(1)
    expect(list[0].kind).toBeNull()
    expect(list[0].reject).toBe('不支持的文件类型')
  })

  it(`超过 ${MAX_FILE_BYTES} 字节的文件标为不可导入，识别结果仍保留`, () => {
    const list = buildBatchCandidates([makeFile('big.mp4', MAX_FILE_BYTES + 1)])
    expect(list[0].kind).toBe('video')
    expect(list[0].reject).toBe('超过 2GB 上传上限')
  })

  it('恰好等于上限的文件可以导入', () => {
    expect(buildBatchCandidates([makeFile('ok.mp4', MAX_FILE_BYTES)])[0].reject).toBe('')
  })
})

describe('planBatchFiles', () => {
  it(`超过 ${BATCH_MAX_FILES} 个时截断，并报告未进入清单的数量`, () => {
    const files = Array.from({ length: BATCH_MAX_FILES + 5 }, (_, i) => makeFile(`f${i}.txt`))
    const { candidates, truncated } = planBatchFiles(files)
    expect(candidates).toHaveLength(BATCH_MAX_FILES)
    expect(truncated).toBe(5)
  })

  it('未超上限时不截断', () => {
    const { candidates, truncated } = planBatchFiles([makeFile('a.txt'), makeFile('b.txt')])
    expect(candidates).toHaveLength(2)
    expect(truncated).toBe(0)
  })
})
