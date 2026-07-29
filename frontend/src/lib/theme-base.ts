/**
 * LiMo — 界面基调管理 (data-base)
 *
 * 控制全局 --tk-* 底色变量。6 种基调覆盖冷暖 + 偏色。
 * data-base 独立于 existing 四轴 (theme × accent × texture × font)
 */

export type BasePreset = 'warm' | 'neutral' | 'cream' | 'cool' | 'rose' | 'sage' | 'jade' | 'amber' | 'pearl' | 'mint' | 'sand' | 'lilac' | 'cloud' | 'blush'

interface BaseEntry {
  key: BasePreset
  label: string
  surface: string
  elevated: string
  hover: string
  border: string
}

export const BASE_OPTIONS: BaseEntry[] = [
  { key: 'warm',    label: '暖灰',   surface: '#F4F1EE', elevated: '#FAF8F5', hover: '#EDEAE6', border: '#D6D3CE' },
  { key: 'neutral', label: '中性白', surface: '#F5F5F5', elevated: '#FCFCFC', hover: '#ECECEC', border: '#D8D8D8' },
  { key: 'cream',   label: '奶油',   surface: '#F9F6F0', elevated: '#FEFCF8', hover: '#F2EDE4', border: '#E0D8CA' },
  { key: 'cool',    label: '冷灰',   surface: '#F2F3F5', elevated: '#F8F9FC', hover: '#E8EAEE', border: '#D4D6DA' },
  { key: 'rose',    label: '玫瑰',   surface: '#F5F1F2', elevated: '#FBF8F9', hover: '#EEE6E8', border: '#D8CED2' },
  { key: 'sage',    label: '鼠尾草', surface: '#F2F4F0', elevated: '#F8FAF7', hover: '#E8ECE6', border: '#D0D6CE' },
  { key: 'jade',    label: '柔绿',   surface: '#EEF2ED', elevated: '#F5F8F4', hover: '#E4EAE3', border: '#CED4CD' },
  { key: 'amber',   label: '琥珀',   surface: '#F9F4EA', elevated: '#FEFBF5', hover: '#F2EBDE', border: '#E0D6C4' },
  { key: 'pearl',   label: '珍珠白', surface: '#F7F6F3', elevated: '#FDFCFB', hover: '#EEEDEA', border: '#DEDCD8' },
  { key: 'mint',    label: '薄荷',   surface: '#EDF4EF', elevated: '#F4FAF6', hover: '#E3EDE6', border: '#CDD8D0' },
  { key: 'sand',    label: '沙色',   surface: '#F6F2EA', elevated: '#FCF9F3', hover: '#EFE9DD', border: '#DDD5C5' },
  { key: 'lilac',   label: '丁香',   surface: '#F3F0F6', elevated: '#FAF8FC', hover: '#ECE7F0', border: '#D6D0DC' },
  { key: 'cloud',   label: '云白',   surface: '#F3F4F8', elevated: '#FAFBFC', hover: '#EAEBF0', border: '#D4D6DC' },
  { key: 'blush',   label: '腮红',   surface: '#F7F1F2', elevated: '#FDF9FA', hover: '#F1E8EA', border: '#DDD0D4' },
]

const STORAGE_KEY = 'mbw_base'

export function getSavedBase(): BasePreset {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    if (v && BASE_OPTIONS.some(b => b.key === v)) return v as BasePreset
  } catch { /* quota */ }
  return 'warm'
}

export function applyBase(b: BasePreset): void {
  document.documentElement.setAttribute('data-base', b)
}

export function saveBase(b: BasePreset): void {
  try { localStorage.setItem(STORAGE_KEY, b) } catch { /* quota */ }
}
