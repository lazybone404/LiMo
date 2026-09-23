/**
 * 文档库 slice — docs / folders / 选中 / 筛选条件。
 *
 * ⚠️ 类型筛选与标签筛选是**两组独立条件**（可同时生效，AND）：
 * - activeType：来源类型（video/pdf/docx…），对应顶部类型行
 * - activeTags：用户自定义标签（多选，AND），对应标签行
 * 曾把二者混在一个 `activeTag` 里（按钮写着「标签筛选」实际过滤的却是 sourceType），
 * 用户给自己打的标签根本无法用来筛选。
 */
import type { StateCreator } from 'zustand'
import type { Store, DocEntry, FolderEntry } from './types'

export interface DocsSlice {
  docs: DocEntry[]
  folders: FolderEntry[]
  currentDocId: string | null
  searchQuery: string
  activeType: string
  activeTags: string[]
  /** 当前文件夹筛选：null = 全部；'' = 未分类；其他 = 文件夹 id。
   *  ⚠️ 三态必须区分开——空串是有效的「未分类」值，不能用 null 代替。 */
  activeFolderId: string | null
  setDocs: (docs: DocEntry[]) => void
  setFolders: (folders: FolderEntry[]) => void
  addDoc: (doc: DocEntry) => void
  selectDoc: (id: string) => void
  setSearchQuery: (q: string) => void
  setActiveType: (t: string) => void
  setActiveTags: (tags: string[]) => void
  setActiveFolderId: (id: string | null) => void
}

export const createDocsSlice: StateCreator<Store, [], [], DocsSlice> = (set) => ({
  docs: [], folders: [], currentDocId: null,
  searchQuery: '', activeType: '', activeTags: [], activeFolderId: null,
  setDocs: (docs) => set({ docs }),
  setFolders: (folders) => set({ folders }),
  addDoc: (doc) => set(s => ({ docs: [doc, ...s.docs] })),
  selectDoc: (id) => { try { localStorage.setItem('mbw_lastDocId', id) } catch { /* quota exceeded */ } set({ currentDocId: id }) },
  setSearchQuery: (q) => set({ searchQuery: q }),
  setActiveType: (t) => set({ activeType: t }),
  setActiveTags: (tags) => set({ activeTags: tags }),
  setActiveFolderId: (id) => set({ activeFolderId: id }),
})
