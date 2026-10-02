/**
 * TabRegistry 单元测试 — 验证注册表驱动 tab 发现、过滤、默认选择
 */
import { describe, it, expect } from 'vitest'
import { TabRegistry } from '../lib/tabRegistry'

describe('TabRegistry', () => {
  it('should return tabs for video source type', () => {
    const tabs = TabRegistry.forSourceType('video')
    const keys = tabs.map(t => t.key)
    expect(keys).toContain('summary')
    expect(keys).toContain('polished')
    expect(keys).toContain('transcript')
    // mindmap depends on content visibility, so not in default list
  })

  it('should return mindmap when content exists', () => {
    const tabs = TabRegistry.forSourceType('video', {
      currentContent: 'some notes content',
      imageResults: {},
    })
    const keys = tabs.map(t => t.key)
    expect(keys).toContain('mindmap')
  })

  it('should not include mindmap when no content', () => {
    const tabs = TabRegistry.forSourceType('webpage', {
      currentContent: null,
      imageResults: {},
    })
    const keys = tabs.map(t => t.key)
    expect(keys).not.toContain('mindmap')
  })

  it('should return image tabs for image source type', () => {
    const tabs = TabRegistry.forSourceType('image')
    const keys = tabs.map(t => t.key)
    expect(keys).toEqual(['original', 'extract', 'table', 'digitize', 'understand', 'ocr'])
  })

  it('should return xlsx tabs', () => {
    const tabs = TabRegistry.forSourceType('xlsx')
    const keys = tabs.map(t => t.key)
    expect(keys).toEqual(['data_preview', 'summary'])
  })

  it('should return epub tabs', () => {
    const tabs = TabRegistry.forSourceType('epub')
    const keys = tabs.map(t => t.key)
    expect(keys).not.toContain('skeleton')  // 骨架面板已砍（一键解读）
    expect(keys).toContain('summary')
    expect(keys).toContain('polished')
    expect(keys).toContain('transcript')
  })

  it('should sort by priority', () => {
    const tabs = TabRegistry.forSourceType('video')
    for (let i = 1; i < tabs.length; i++) {
      expect(tabs[i].priority).toBeGreaterThanOrEqual(tabs[i - 1].priority)
    }
  })

  it('should return default tab per source type', () => {
    expect(TabRegistry.defaultFor('pdf')).toBe('polished')
    expect(TabRegistry.defaultFor('video')).toBe('summary')
    expect(TabRegistry.defaultFor('image')).toBe('original')
    expect(TabRegistry.defaultFor('xlsx')).toBe('data_preview')
    expect(TabRegistry.defaultFor('epub')).toBe('summary')  // 有产物默认 summary（一键解读）
    expect(TabRegistry.defaultFor('webpage')).toBe('summary')
  })

  it('should get tab by key', () => {
    const tab = TabRegistry.get('summary')
    expect(tab).toBeDefined()
    expect(tab!.key).toBe('summary')
    expect(tab!.contentSource).toBe('notes')
    expect(tab!.htmlMode).toBe('notes')
  })

  it('should have correct export config for all tabs', () => {
    for (const tab of TabRegistry.all()) {
      expect(typeof tab.contentSource).toBe('string')
      expect(typeof tab.htmlMode).toBe('string')
      expect(typeof tab.exportSuffix).toBe('string')
      expect(typeof tab.exportLabel).toBe('string')
    }
  })

  it('should have processingKinds for summary', () => {
    const tab = TabRegistry.get('summary')!
    expect(tab.processingKinds).toContain('generate')
  })

  it('should have showBanner for transcript', () => {
    const tab = TabRegistry.get('transcript')!
    expect(tab.showBanner).toBe(true)
  })

  it('should have showTOC for polished', () => {
    const tab = TabRegistry.get('polished')!
    expect(tab.showTOC).toBe(true)
  })

  it('should have showVersionSwitcher for summary and polished', () => {
    expect(TabRegistry.get('summary')!.showVersionSwitcher).toBe('notes')
    expect(TabRegistry.get('polished')!.showVersionSwitcher).toBe('polished')
  })
})

describe('TabRegistry — composite（多篇合成）', () => {
  // 对照实验（已验证有区分力）：把 'composite' 加进 `DOC_TYPES`（那会被 `ALL_TEXT`
  // 展开给 polished），或给 mindmap 的 sourceTypes 也加上 → 本组两条**都会红**
  // （多出 polished / mindmap）——这正是「顺手加进 DOC_TYPES 最省事」的代价。
  it('composite 恰好只有 笔记 + 转录 两个 tab', () => {
    expect(TabRegistry.forSourceType('composite').map(t => t.key)).toEqual(['summary', 'transcript'])
  })

  it('composite 的 defaultFor 是 summary', () => {
    // ⚠️ 这条与上面那条**测的不是同一件事**：列出哪些 tab ≠ 生成完停在哪个 tab。
    // 只加 `sourceTypes` 不加 `defaultFor` 时，上一条照样绿，而用户点完生成会停在
    // 「转录」tab 上（得手动点一次「笔记」）。
    expect(TabRegistry.defaultFor('composite')).toBe('summary')
  })
})

describe('TabRegistry re-registration', () => {
  it('should override existing tab', () => {
    const before = TabRegistry.count
    TabRegistry.register({
      key: 'test-override', label: '测试覆盖',
      sourceTypes: ['test'],
      priority: 99,
      contentSource: 'notes', htmlMode: '', exportSuffix: '', exportLabel: '测试',
    })
    expect(TabRegistry.count).toBe(before + 1)
    const t = TabRegistry.get('test-override')
    expect(t).toBeDefined()

    // Override it
    TabRegistry.register({
      key: 'test-override', label: '测试覆盖2',
      sourceTypes: ['test', 'other'],
      priority: 50,
      contentSource: 'rawText', htmlMode: 'test', exportSuffix: '-test', exportLabel: '测试2',
    })
    expect(TabRegistry.count).toBe(before + 1) // same count
    const t2 = TabRegistry.get('test-override')
    expect(t2!.sourceTypes).toEqual(['test', 'other'])
    expect(t2!.label).toBe('测试覆盖2')
  })
})
