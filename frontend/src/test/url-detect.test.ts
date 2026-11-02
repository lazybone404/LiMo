// 导入框的 URL 识别 —— 判据的两种错方向代价完全不对称：
//   漏判（当纯文本）→ 用户手动补个 https:// 或改用别的入口；
//   误判（当 URL）  → **一篇正文被发去下载**，内容直接没了，且界面上只显示「下载中…」。
// 所以下面两组用例同样重要：能认出来的一批，和**必须不被认成 URL** 的一批。
import { describe, it, expect } from 'vitest'
import { detectImportUrl } from '../lib/url-detect'

describe('detectImportUrl · 直接就是 URL', () => {
  it.each([
    'https://www.bilibili.com/video/BV1xx411c7mD',
    'http://example.com/a?b=c',
    'HTTPS://B23.TV/abcdefg',
  ])('%s 原样返回', (u) => {
    expect(detectImportUrl(u)).toBe(u)
  })

  it('前后空白被 trim 掉', () => {
    expect(detectImportUrl('  https://b23.tv/abc  ')).toBe('https://b23.tv/abc')
  })
})

describe('detectImportUrl · 分享文案包裹的 URL', () => {
  it('B 站 App 分享文案：抽出 URL、丢掉标题', () => {
    expect(detectImportUrl('【标题】某支视频 https://b23.tv/abcdefg')).toBe('https://b23.tv/abcdefg')
  })

  it('抖音分享文案', () => {
    // 尾部 `/` 保留 —— 它是路径的一部分，不是包裹符（剥的是 `）`, `。` 这类）
    expect(detectImportUrl('7.94 复制打开抖音，看看【我】的作品 https://v.douyin.com/abc/ 复制此链接'))
      .toBe('https://v.douyin.com/abc/')
  })

  it('URL 尾部的包裹标点被剥掉', () => {
    expect(detectImportUrl('看这个（https://b23.tv/abc）')).toBe('https://b23.tv/abc')
  })

  it('⚠️ 多行正文里引用一个链接 → 仍是纯文本', () => {
    const text = '这是我整理的一段笔记。\n参考 https://b23.tv/abc\n后面还有很多内容。'
    expect(detectImportUrl(text)).toBeNull()
  })

  it('⚠️ 单行但正文很长、链接只是其中一部分 → 纯文本', () => {
    const text = '这段话我写了很多很多字用来描述我的想法和背景，' +
      '以及为什么这件事重要，然后顺便贴了个链接 https://b23.tv/abc 供参考'
    expect(detectImportUrl(text)).toBeNull()
  })
})

describe('detectImportUrl · 丢了协议的裸形态', () => {
  it('www. 开头', () => {
    expect(detectImportUrl('www.bilibili.com/video/BV1xx411c7mD'))
      .toBe('https://www.bilibili.com/video/BV1xx411c7mD')
  })

  it('短链（有路径）', () => {
    expect(detectImportUrl('b23.tv/abcdefg')).toBe('https://b23.tv/abcdefg')
  })

  it('⚠️ 裸文件名不是 URL（没有 /，不能当域名）', () => {
    expect(detectImportUrl('README.md')).toBeNull()
    expect(detectImportUrl('config.yaml')).toBeNull()
  })

  it('⚠️ 无路径的裸域名不认（无法与普通词区分，宁可不认）', () => {
    expect(detectImportUrl('foo.com')).toBeNull()
  })

  it('⚠️ 带空格的短语不认', () => {
    expect(detectImportUrl('see www.example.com for details')).toBeNull()
  })
})

describe('detectImportUrl · 纯文本', () => {
  it.each([
    '',
    '   ',
    '这是一段普通文本',
    'hello world',
  ])('「%s」→ null', (t) => {
    expect(detectImportUrl(t)).toBeNull()
  })
})
