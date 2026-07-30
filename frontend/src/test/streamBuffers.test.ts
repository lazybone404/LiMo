import { describe, it, expect, beforeEach, vi } from 'vitest'
import {
  getStreamBuffer, appendStreamBuffer, setStreamBuffer,
  deleteStreamBuffer, hasStreamBuffer,
  scheduleFlush, flushImmediate,
} from '../store/streamBuffers'

describe('streamBuffers', () => {
  beforeEach(() => {
    // 清理所有缓冲区
    deleteStreamBuffer('test_key')
    deleteStreamBuffer('docA__generate_notes')
    deleteStreamBuffer('docA__generate_polished')
    deleteStreamBuffer('docB__generate_notes')
  })

  describe('getStreamBuffer', () => {
    it('returns empty string for non-existent key', () => {
      expect(getStreamBuffer('nonexistent')).toBe('')
    })
  })

  describe('setStreamBuffer / getStreamBuffer', () => {
    it('sets and retrieves buffer content', () => {
      setStreamBuffer('test_key', 'hello')
      expect(getStreamBuffer('test_key')).toBe('hello')
    })

    it('overwrites existing content', () => {
      setStreamBuffer('test_key', 'old')
      setStreamBuffer('test_key', 'new')
      expect(getStreamBuffer('test_key')).toBe('new')
    })
  })

  describe('appendStreamBuffer', () => {
    it('creates buffer if key does not exist', () => {
      appendStreamBuffer('test_key', 'first')
      expect(getStreamBuffer('test_key')).toBe('first')
    })

    it('appends to existing buffer', () => {
      setStreamBuffer('test_key', 'hello')
      appendStreamBuffer('test_key', ' world')
      expect(getStreamBuffer('test_key')).toBe('hello world')
    })

    it('handles multiple appends', () => {
      appendStreamBuffer('test_key', 'a')
      appendStreamBuffer('test_key', 'b')
      appendStreamBuffer('test_key', 'c')
      expect(getStreamBuffer('test_key')).toBe('abc')
    })
  })

  describe('deleteStreamBuffer', () => {
    it('deletes buffer content', () => {
      setStreamBuffer('test_key', 'data')
      deleteStreamBuffer('test_key')
      expect(getStreamBuffer('test_key')).toBe('')
    })

    it('no-ops for non-existent key', () => {
      expect(() => deleteStreamBuffer('nonexistent')).not.toThrow()
    })
  })

  describe('hasStreamBuffer', () => {
    it('returns false for non-existent key', () => {
      expect(hasStreamBuffer('nonexistent')).toBe(false)
    })

    it('returns true after setting content', () => {
      setStreamBuffer('test_key', 'data')
      expect(hasStreamBuffer('test_key')).toBe(true)
    })

    it('returns false after deletion', () => {
      setStreamBuffer('test_key', 'data')
      deleteStreamBuffer('test_key')
      expect(hasStreamBuffer('test_key')).toBe(false)
    })
  })

  describe('多任务并发隔离', () => {
    it('不同文档的缓冲区相互独立', () => {
      appendStreamBuffer('docA__generate_notes', '# Doc A Notes')
      appendStreamBuffer('docB__generate_notes', '# Doc B Notes')

      expect(getStreamBuffer('docA__generate_notes')).toBe('# Doc A Notes')
      expect(getStreamBuffer('docB__generate_notes')).toBe('# Doc B Notes')
    })

    it('同一文档不同产物缓冲区独立', () => {
      appendStreamBuffer('docA__generate_notes', 'notes content')
      appendStreamBuffer('docA__generate_polished', 'polished content')

      expect(getStreamBuffer('docA__generate_notes')).toBe('notes content')
      expect(getStreamBuffer('docA__generate_polished')).toBe('polished content')
    })

    it('删除一个文档不影响另一个', () => {
      setStreamBuffer('docA__generate_notes', 'A')
      setStreamBuffer('docB__generate_notes', 'B')

      deleteStreamBuffer('docA__generate_notes')

      expect(getStreamBuffer('docA__generate_notes')).toBe('')
      expect(getStreamBuffer('docB__generate_notes')).toBe('B')
    })
  })

  describe('scheduleFlush', () => {
    it('calls flush function after debounce', async () => {
      vi.useFakeTimers()
      const flush = vi.fn()

      scheduleFlush(flush)
      expect(flush).not.toHaveBeenCalled() // 尚未触发

      vi.advanceTimersByTime(120)
      expect(flush).toHaveBeenCalledOnce()

      vi.useRealTimers()
    })

    it('deduplicates multiple calls within debounce window', async () => {
      vi.useFakeTimers()
      const flush = vi.fn()

      scheduleFlush(flush)
      scheduleFlush(flush)
      scheduleFlush(flush)

      vi.advanceTimersByTime(120)
      expect(flush).toHaveBeenCalledOnce() // 只触发一次

      vi.useRealTimers()
    })
  })

  describe('flushImmediate', () => {
    it('cancels debounce and flushes immediately', async () => {
      vi.useFakeTimers()
      const flush = vi.fn()

      scheduleFlush(flush)
      flushImmediate(flush)

      expect(flush).toHaveBeenCalledOnce() // 立即触发

      // 确保去抖已取消
      vi.advanceTimersByTime(200)
      expect(flush).toHaveBeenCalledOnce() // 不再额外触发

      vi.useRealTimers()
    })
  })
})
