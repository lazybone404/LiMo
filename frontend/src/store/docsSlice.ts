/**
 * 文档库 slice — docs/currentDocId/searchQuery/activeTag。
 */
import type { StateCreator } from 'zustand'
import type { Store, DocEntry } from './types'

export interface DocsSlice {
  docs: DocEntry[]
  currentDocId: string | null
  searchQuery: string
  activeTag: string
  setDocs: (docs: DocEntry[]) => void
  addDoc: (doc: DocEntry) => void
  selectDoc: (id: string) => void
  setSearchQuery: (q: string) => void
  setActiveTag: (tag: string) => void
}

export const createDocsSlice: StateCreator<Store, [], [], DocsSlice> = (set) => ({
  docs: [], currentDocId: null, searchQuery: '', activeTag: '',
  setDocs: (docs) => set({ docs }),
  addDoc: (doc) => set(s => ({ docs: [doc, ...s.docs] })),
  selectDoc: (id) => { try { localStorage.setItem('mbw_lastDocId', id) } catch { /* quota exceeded */ } set({ currentDocId: id }) },
  setSearchQuery: (q) => set({ searchQuery: q }),
  setActiveTag: (tag) => set({ activeTag: tag }),
})
