/**
 * 文件夹树纯函数 —— 表驱动读**跨语言共享 fixture**。
 *
 * 这个文件的价值几乎全在 fixture 本身上：树形有五条不变量，前端 TS 与后端 Python
 * 各一份实现（Python 不能被 TS import），它们必然分开演化。这份 JSON 是两侧
 * **唯一的一致性证据** —— 后端 `tests/test_folder_tree.py` 读同一份文件。
 * 「两边写得像」不是证据。
 *
 * ⚠️ 前端**只消费 `trees`**：`parsePaths` 那段是后端专用的（路径解析只活在后端
 * `folder_tree.py::parse_path`，建夹走 `/api/folders/ensure-path`）。这不是遗漏 ——
 * 前端一份路径解析都没有，漂移就不可能发生。
 *
 * 对照实验（每条都对应一个「看起来对、其实错」的实现）：
 *   A. 不断环 → `preorder` 里两个节点互为父子，`DocList` 一条都渲染不出来。
 *      环还可能让 `descendantIds` 死循环。
 *   B. 断点取「遍历起点」而非「数组下标最小者」→ 同一份数据每次启动树形可能不同。
 *   C. 悬空 parentId 不归化 → 该夹既不在根级、也不在任何父级下 ⇒ **从列表里消失**
 *      （只能靠搜索找到，用户会以为夹子丢了）。
 *   D. 兄弟顺序按 id 排/按名字排 → 与用户建夹顺序不符，且每次刷新都可能变。
 *   E. `descendantIds` 含自身 → 「子树共 N 篇」自增 1，而且没人会注意到。
 *   F. `totalCount` 只算直接子级 → 父夹显示 1 篇，点进去却是 9 篇。
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import {
  ROOT,
  buildFolderTree,
  normalizeParents,
  pathKey,
  type FolderNode,
} from '../components/doc-library/folderTree'

/**
 * ⚠️ 用 `process.cwd()` 而非 `import.meta.url`：本文件跑在 jsdom 环境。
 * 与 `markdownSections.test.ts` / `readingLayout.test.tsx` 同款。
 */
const FIXTURE_PATH = resolve(process.cwd(), 'src/test/fixtures/folder-tree.json')
const FIXTURE = JSON.parse(readFileSync(FIXTURE_PATH, 'utf-8')) as {
  trees: {
    name: string
    folders: FolderNode[]
    directCounts: Record<string, number>
    expect: {
      preorder: string[]
      paths: Record<string, string>
      totalCounts: Record<string, number>
      descendants: Record<string, string[]>
      ancestors: Record<string, string[]>
    }
  }[]
  parsePaths: unknown[]
}

describe('buildFolderTree — 跨语言共享 fixture', () => {
  it('fixture 覆盖到五条不变量（防止用例被删空后整组测试静默变成空跑）', () => {
    expect(FIXTURE.trees.length).toBeGreaterThanOrEqual(6)
    const names = FIXTURE.trees.map(t => t.name).join('\n')
    expect(names).toContain('无 parentId')  // 零迁移路径
    expect(names).toContain('悬空')          // 不变量 1
    expect(names).toContain('2 环')          // 不变量 2
    expect(names).toContain('3 环')
    expect(names).toContain('尾巴')
    expect(names).toContain('三层嵌套')      // 不变量 3
  })

  for (const c of FIXTURE.trees) {
    it(`树结构：${c.name}`, () => {
      const tree = buildFolderTree(c.folders, c.directCounts)
      expect(tree.preorder).toEqual(c.expect.preorder)

      for (const [id, want] of Object.entries(c.expect.paths)) {
        expect(tree.pathOf.get(id)).toBe(want)
      }
      for (const [id, want] of Object.entries(c.expect.totalCounts)) {
        expect(tree.totalCountOf.get(id)).toBe(want)
      }
      for (const [id, want] of Object.entries(c.expect.descendants)) {
        expect([...tree.descendantIds(id)].sort()).toEqual([...want].sort())
      }
      for (const [id, want] of Object.entries(c.expect.ancestors)) {
        expect(tree.ancestorIds(id)).toEqual(want)
      }
    })
  }

  it('前序是拓扑序：任何非根节点的父级都排在它前面（不变量 3）', () => {
    for (const c of FIXTURE.trees) {
      const tree = buildFolderTree(c.folders, c.directCounts)
      const seen = new Set<string>()
      for (const id of tree.preorder) {
        const p = tree.parentOf.get(id) || ROOT
        if (p) expect(seen.has(p), `${c.name}: ${id} 排在父级 ${p} 之前`).toBe(true)
        seen.add(id)
      }
    }
  })

  it('后代不含自身（不变量 4）', () => {
    const folders: FolderNode[] = [
      { id: 'fd_a', name: '甲' },
      { id: 'fd_b', name: '乙', parentId: 'fd_a' },
    ]
    const tree = buildFolderTree(folders)
    expect(tree.descendantIds('fd_a').has('fd_a')).toBe(false)
    expect([...tree.descendantIds('fd_a')]).toEqual(['fd_b'])
  })

  it('断环点跟着数组顺序走，而不是遍历起点', () => {
    const a: FolderNode = { id: 'fd_a', name: '甲', parentId: 'fd_b' }
    const b: FolderNode = { id: 'fd_b', name: '乙', parentId: 'fd_a' }

    const p1 = normalizeParents([a, b])
    expect(p1.get('fd_a')).toBe(ROOT)
    expect(p1.get('fd_b')).toBe('fd_a')

    const p2 = normalizeParents([b, a])
    expect(p2.get('fd_b')).toBe(ROOT)
    expect(p2.get('fd_a')).toBe('fd_b')
  })

  it('未分类不是实体：树里永远没有空 id 节点（不变量 5）', () => {
    for (const c of FIXTURE.trees) {
      const tree = buildFolderTree(c.folders, c.directCounts)
      expect(tree.byId.has(ROOT)).toBe(false)
      expect(tree.preorder).not.toContain(ROOT)
    }
  })

  it('不传 directCounts 时 totalCountOf 退回后端给的 totalCount', () => {
    const tree = buildFolderTree([
      { id: 'fd_a', name: '技术', count: 1, totalCount: 6 },
      { id: 'fd_b', name: '前端', parentId: 'fd_a', count: 5, totalCount: 5 },
    ])
    expect(tree.totalCountOf.get('fd_a')).toBe(6)
    expect(tree.totalCountOf.get('fd_b')).toBe(5)
  })

  it('pathOf 只覆盖真实存在的夹（不存在的 id 查不到，而不是空串）', () => {
    const folders: FolderNode[] = [
      { id: 'fd_a', name: '技术' },
      { id: 'fd_b', name: '前端', parentId: 'fd_a' },
    ]
    const tree = buildFolderTree(folders)
    expect(tree.pathOf.get('fd_b')).toBe('技术 › 前端')
    expect(tree.pathOf.has('fd_nope')).toBe(false)
  })

  it('pathKey 是展示性归一（权威匹配在后端 path_key）', () => {
    expect(pathKey('  技术   前端 ')).toBe('技术 前端')
    expect(pathKey('ABC')).toBe('abc')
  })
})
