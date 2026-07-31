/**
 * 对话 slice — 对话管理 + 生成状态 + AI 浮动对话。
 */
import type { StateCreator } from 'zustand'
import type { Store, Conversation, ChatMessage } from './types'

export interface ChatSlice {
  // ── 对话管理 ──
  conversations: Conversation[]
  currentConversationId: string | null
  setConversations: (list: Conversation[]) => void
  addConversation: (conv: Conversation) => void
  removeConversation: (id: string) => void
  renameConversation: (id: string, title: string) => void
  setCurrentConversationId: (id: string | null) => void

  // ── 生成状态 ──
  generating: boolean
  setGenerating: (v: boolean) => void
  factChecking: boolean
  setFactChecking: (v: boolean) => void

  // ── AI 浮动对话 ──
  chatMessages: ChatMessage[]
  chatLoading: boolean
  aiFloating: boolean
  aiFloatingPos: { x: number; y: number }
  aiFloatingSize: { width: number; height: number }
  addChatMessage: (msg: ChatMessage) => void
  setChatMessages: (msgs: ChatMessage[]) => void
  setChatLoading: (v: boolean) => void
  setAiFloating: (v: boolean) => void
  setAiFloatingPos: (pos: { x: number; y: number }) => void
  setAiFloatingSize: (size: { width: number; height: number }) => void
}

export const createChatSlice: StateCreator<Store, [], [], ChatSlice> = (set) => ({
  conversations: [], currentConversationId: null,
  setConversations: (list) => set({ conversations: list }),
  addConversation: (conv) => set(s => ({ conversations: [conv, ...s.conversations] })),
  removeConversation: (id) => set(s => ({ conversations: s.conversations.filter(c => c.id !== id), currentConversationId: s.currentConversationId === id ? null : s.currentConversationId })),
  renameConversation: (id, title) => set(s => ({ conversations: s.conversations.map(c => c.id === id ? { ...c, title } : c) })),
  setCurrentConversationId: (id) => { if (id) try { localStorage.setItem('mbw_lastConvId', id) } catch {/*quota*/} else try { localStorage.removeItem('mbw_lastConvId') } catch {/*quota*/}; set({ currentConversationId: id }) },

  generating: false,
  setGenerating: (v) => set({ generating: v }),
  factChecking: false,
  setFactChecking: (v) => set({ factChecking: v }),

  chatMessages: [], chatLoading: false,
  aiFloating: false,
  aiFloatingPos: { x: window.innerWidth - 500, y: window.innerHeight - 560 },
  aiFloatingSize: { width: 480, height: 540 },
  addChatMessage: (msg) => set(s => ({ chatMessages: [...s.chatMessages, msg] })),
  setChatMessages: (msgs) => set({ chatMessages: msgs }),
  setChatLoading: (v) => set({ chatLoading: v }),
  setAiFloating: (v) => set({ aiFloating: v }),
  setAiFloatingPos: (pos) => set({ aiFloatingPos: pos }),
  setAiFloatingSize: (size) => set({ aiFloatingSize: size }),
})
