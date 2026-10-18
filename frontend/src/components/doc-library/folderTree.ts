/**
 * 文件夹树 —— 前端这一侧的结构层。
 *
 * ⚠️ 后端有一份同构实现（`backend/app/storage/folder_tree.py`），因为 Python 无法被
 * TS import。两份靠**同一份共享 fixture** 钉住：
 * `src/test/fixtures/folder-tree.json`（后端测试直接读这个路径）。
 *
 * ⚠️ **本文件不做路径解析**。`parse_path` 只活在后端一处，建夹统一走
 * `POST /api/folders/ensure-path`（把原始串 + folders 上下文整个交给后端）。
 * 两份实现要靠 fixture 防漂移；一份都没有，漂移就不可能发生。
 *
 * 五条不变量（与 `folder_tree.py` 文件头**逐字同源**）：
 * 1. 缺 `parentId` / 指向不存在的 id / 指向自己 → 视为根。
 * 2. 环：沿 parent 上行检测，在环中 **folders 数组下标最小**的节点处断开，令其为根
 *    （确定性，不是随机）。
 * 3. 兄弟顺序 = folders 数组顺序；前序遍历保证父一定排在子之前。
 * 4. `descendantIds` 不含自身；`totalCount` = 直接文档数 + 所有后代求和。
 * 5. 未分类不是实体，永不在树里，固定排在列表最后。
 */

/** 本模块只要求这几个字段；`FolderEntry` 与测试里手搓的最小对象都能传进来。 */
export interface FolderNode {
  id: string
  name: string
  parentId?: string
  /** 后端现算的**直接**子级文档数（不含子树） */
  count?: number
  /** 后端现算的含子树总数 */
  totalCount?: number
}

export interface FolderTree {
  byId: Map<string, FolderNode>
  /** 规范化后的父级（`''` = 根）；脏值已按不变量 1/2 归化 */
  parentOf: Map<string, string>
  roots: string[]
  childrenOf: Map<string, string[]>
  /** 前序遍历（不变量 3） */
  preorder: string[]
  depthOf: Map<string, number>
  /** `父 › 子`（分隔符用 `›`，见下） */
  pathOf: Map<string, string>
  descendantIds: (id: string) => Set<string>
  /** 从根到直接父 */
  ancestorIds: (id: string) => string[]
  /** 含子树的总文档数（不变量 4）。没传 `directCounts` 时退回后端给的 `totalCount` */
  totalCountOf: Map<string, number>
}

export const ROOT = ''

/**
 * 名字归一：`trim` + 空白折叠 + 小写。前端只拿它做「同一份列表里避免重复新建」这类
 * 展示性判断；**匹配的权威实现**在后端 `path_key`（大小写规则是 `casefold`，
 * 与 JS 的 `toLowerCase` 在少数语种上不同 —— 不在这里追求逐字节等价）。
 */
export function pathKey(name: string): string {
  return (name || '').trim().replace(/\s+/g, ' ').toLowerCase()
}

/** 不变量 1 + 2：把脏 parentId 归化掉，并打断环。 */
export function normalizeParents(folders: FolderNode[]): Map<string, string> {
  const byId = new Map(folders.map(f => [f.id, f]))
  const order = new Map(folders.map((f, i) => [f.id, i]))
  const parent = new Map<string, string>()
  for (const f of folders) {
    const pid = f.parentId || ROOT
    parent.set(f.id, pid && pid !== f.id && byId.has(pid) ? pid : ROOT)
  }

  // 断环：每个环只断一点就足够破坏它，断点取环中 **数组下标最小** 的那个。
  // 随机 / 取决于遍历顺序会让每次启动的树形不同，用户会以为夹子在自己乱跳。
  for (const start of [...parent.keys()]) {
    const seen = new Map<string, number>()
    const path: string[] = []
    let cur = start
    while (cur && !seen.has(cur)) {
      seen.set(cur, path.length)
      path.push(cur)
      cur = parent.get(cur) || ROOT
    }
    if (cur && seen.has(cur)) {
      const cycle = path.slice(seen.get(cur)!)
      let root = cycle[0]
      for (const n of cycle) {
        if ((order.get(n) ?? 0) < (order.get(root) ?? 0)) root = n
      }
      parent.set(root, ROOT)
    }
  }
  return parent
}

function buildChildren(folders: FolderNode[], parent: Map<string, string>): Map<string, string[]> {
  const out = new Map<string, string[]>()
  for (const f of folders) {
    const p = parent.get(f.id) || ROOT
    const arr = out.get(p)
    if (arr) arr.push(f.id)
    else out.set(p, [f.id])
  }
  return out
}

function preorderOf(children: Map<string, string[]>): string[] {
  const out: string[] = []
  const stack = [...(children.get(ROOT) || [])].reverse()
  while (stack.length) {
    const id = stack.pop()!
    out.push(id)
    const kids = children.get(id)
    if (kids) {
      for (let i = kids.length - 1; i >= 0; i--) stack.push(kids[i])
    }
  }
  return out
}

/**
 * @param folders      后端返回的文件夹清单（顺序即兄弟顺序）
 * @param directCounts 本地派生的**直接**计数（`countByFolder().byFolder`）。
 *                     不传时 `totalCountOf` 退回后端给的 `totalCount` —— 拉取后
 *                     未发生移动的那段时间里，两者一致。
 */
export function buildFolderTree(
  folders: FolderNode[],
  directCounts?: Map<string, number> | Record<string, number>,
): FolderTree {
  const byId = new Map(folders.map(f => [f.id, f]))
  const parentOf = normalizeParents(folders)
  const childrenOf = buildChildren(folders, parentOf)
  const order = preorderOf(childrenOf)

  const depthOf = new Map<string, number>()
  for (const id of order) {
    const p = parentOf.get(id) || ROOT
    depthOf.set(id, p === ROOT ? 0 : (depthOf.get(p) ?? 0) + 1)
  }

  const pathOf = new Map<string, string>()
  const ancestorIds = (id: string): string[] => {
    const out: string[] = []
    const seen = new Set<string>()
    let cur = parentOf.get(id) || ROOT
    while (cur && !seen.has(cur)) {
      seen.add(cur)
      out.push(cur)
      cur = parentOf.get(cur) || ROOT
    }
    return out.reverse()
  }
  for (const f of folders) {
    const names = ancestorIds(f.id).map(a => byId.get(a)!.name)
    names.push(f.name)
    // ⚠️ 分隔符用 `›` 而不是 `/`：文件夹名本身可以含 `/`（历史数据里就有），
    // 用 `/` 会让「名字叫 前端/后端 的夹」和「前端 下的 后端」在界面上无法区分。
    pathOf.set(f.id, names.join(' › '))
  }

  const descendantIds = (id: string): Set<string> => {
    const out = new Set<string>()
    const stack = [...(childrenOf.get(id) || [])]
    while (stack.length) {
      const cur = stack.pop()!
      if (out.has(cur)) continue
      out.add(cur)
      const kids = childrenOf.get(cur)
      if (kids) stack.push(...kids)
    }
    out.delete(id)
    return out
  }

  const totalCountOf = new Map<string, number>()
  if (directCounts) {
    const get = directCounts instanceof Map
      ? (k: string) => directCounts.get(k) ?? 0
      : (k: string) => (directCounts as Record<string, number>)[k] ?? 0
    for (const f of folders) totalCountOf.set(f.id, get(f.id))
    // 前序保证后代总在祖先之后，反转后后代先处理（与 Python 的 total_counts 同构）
    for (let i = order.length - 1; i >= 0; i--) {
      const id = order[i]
      const p = parentOf.get(id) || ROOT
      if (p) totalCountOf.set(p, (totalCountOf.get(p) ?? 0) + (totalCountOf.get(id) ?? 0))
    }
  } else {
    for (const f of folders) totalCountOf.set(f.id, f.totalCount ?? f.count ?? 0)
  }

  return {
    byId, parentOf, childrenOf,
    roots: childrenOf.get(ROOT) || [],
    preorder: order, depthOf, pathOf, descendantIds, ancestorIds, totalCountOf,
  }
}
