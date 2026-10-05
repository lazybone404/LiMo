/**
 * P2-15 — 引擎能力位标签由后端下发并被消费。
 *
 * **修复的是什么**：`GET /api/capabilities` 的 `engineLabels` 一直有下发，
 * `api/core.ts` 也声明了字段，但**没有任何消费方** → `EngineSelect` 把引擎的
 * `capabilities` 原样 join，用户在下拉里看到裸英文（`pypdfium2 · text image pdf`）。
 *
 * ⚠️ 这个缺陷**不是「少写了一处翻译」**，而是「下发了一条契约没人接」——
 * 补齐 `EngineSelect` 只是接线；真正要守的是「以后新加的能力位别再退化成英文」，
 * 而那靠的是后端那张表完整（`backend/tests/test_engine_capability_labels.py`
 * 扫全部引擎类做包含断言）。本文件守前端这一侧。
 *
 * ⚠️ 与 `lib/modelCaps.ts` 的 `capLabel` **是两套表**（模型能力位 vs 引擎能力位），
 * 有交集但含义不同，见两个文件各自的 docstring。
 */
import { describe, it, expect } from 'vitest'
import { optionLabel } from '../components/gen-panel/EngineSelect'
import type { EngineOption } from '../components/gen-panel/EngineSelect'
import engineSelectSrc from '../components/gen-panel/EngineSelect.tsx?raw'
import modelCapsSrc from '../lib/modelCaps.ts?raw'

/** 后端 `core/registry.ENGINE_CAPABILITY_LABELS` 的镜像（手抄，仅用于断言语义） */
const LABELS: Record<string, string> = {
  text: '文字', table: '表格', image: '图片',
  ocr: 'OCR', pdf: 'PDF', office: '文档', asr: '语音',
}

const pick = (o: Partial<EngineOption>): EngineOption =>
  ({ id: 1, provider: 'p', name: 'n', desc: '', ...o }) as EngineOption

describe('P2-15 — 引擎能力位标签', () => {
  it('★ 能力位被翻译成中文（不再是裸英文）', () => {
    const text = optionLabel(pick({ name: 'pypdfium2', capabilities: ['text', 'image', 'pdf'] }), LABELS)
    expect(text).toContain('文字 图片 PDF')
    expect(text).not.toContain('text image pdf')
  })

  it('全部 7 个能力位都有译名（表内自洽）', () => {
    for (const [k, v] of Object.entries(LABELS)) {
      expect(optionLabel(pick({ name: 'e', capabilities: [k] }), LABELS)).toContain(v)
    }
  })

  it('标签表未到位时回退英文 key（可见，不静默变空）', () => {
    // caps 还没拉到 / 后端漏发该字段时，宁可显示英文也不要空徽标
    expect(optionLabel(pick({ name: 'e', capabilities: ['ocr'] }), undefined)).toContain('ocr')
    // 后端新增能力位而表还没跟上时同理
    expect(optionLabel(pick({ name: 'e', capabilities: ['brandnew'] }), LABELS)).toContain('brandnew')
  })

  it('其余字段拼接不受影响（displayName / license / desc）', () => {
    const text = optionLabel(pick({
      name: 'raw', displayName: '展示名', license: 'MIT', desc: '说明', capabilities: ['asr'],
    }), LABELS)
    expect(text).toBe('展示名 [MIT] · 语音 — 说明')
  })

  it('无 capabilities 时不产生多余分隔符', () => {
    expect(optionLabel(pick({ name: 'e', desc: 'd' }), LABELS)).toBe('e — d')
    expect(optionLabel(pick({ name: 'e' }), LABELS)).toBe('e')
  })
})

describe('★ 源码守卫：接线方式', () => {
  it('消费 caps.engineLabels（而不是自己再存一份表）', () => {
    expect(engineSelectSrc).toContain('s.capabilities?.engineLabels')
  })

  it('不再裸 join capabilities', () => {
    // 剥注释再判——文档注释里**刻意**保留了旧写法当反例（模式 #54 配套细节）
    const code = engineSelectSrc.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    expect(code).not.toMatch(/capabilities\?*\.join\(/)
    expect(code).toMatch(/\.map\(c => capLabels/)
  })

  it('没有把模型能力位表搬过来（两套词汇不混用）', () => {
    // 「思考档位」「PDF 直传」是**模型**能力位的文案，出现在这里就是拿了错表
    expect(engineSelectSrc).not.toContain('思考档位')
    expect(engineSelectSrc).not.toContain('PDF 直传')
    expect(modelCapsSrc).toContain('思考档位')  // 自证：那两个词确实是模型侧的
  })
})
