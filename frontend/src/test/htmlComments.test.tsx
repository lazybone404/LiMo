/**
 * HTML 注释不进渲染（`lib/markdown-plugins.ts::rehypeStripComments`）。
 *
 * 背景：react-markdown 在**不启用** `allowDangerousHtml` / `rehype-raw` 时，会把
 * `<!-- … -->` 当成普通文本**转义**后输出（`&lt;!-- … --&gt;`）—— 于是产物首行的
 * `<!-- limo-meta {…} -->` 与模型写进正文的注释都会**原样显示成一行字**。
 *
 * ⚠️ 与 `markdownMarkers.test.ts` 的分工：那个测的是 `limo-block` **标记行**的解析与
 * 剥除（字符串层，`parseMarkers`）；这里测的是**其余任意 HTML 注释**在渲染层不出现。
 */
import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import ReactMarkdown from 'react-markdown'
import { REMARK_PLUGINS, REHYPE_PLUGINS, REHYPE_PLUGINS_PLAIN } from '../lib/markdown-plugins'

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function renderMd(md: string, rehype: any[] = REHYPE_PLUGINS): string {
  const { container } = render(
    <ReactMarkdown remarkPlugins={REMARK_PLUGINS} rehypePlugins={rehype}>{md}</ReactMarkdown>,
  )
  return container.innerHTML
}

describe('HTML 注释不进渲染', () => {
  it('块级注释（独占一行）被剥掉，前后正文完好', () => {
    const html = renderMd('前文\n\n<!-- ZZCOMMENT1 -->\n\n后文')
    expect(html).toContain('前文')
    expect(html).toContain('后文')
    expect(html).not.toContain('ZZCOMMENT1')
  })

  it('产物自证头形态（limo-meta + JSON）被剥掉', () => {
    const html = renderMd(
      '<!-- limo-meta {"type":"artifact","identity":{"mode":"notes"}} -->\n\n# 标题\n\n正文',
    )
    expect(html).toContain('标题')
    expect(html).toContain('正文')
    expect(html).not.toContain('limo-meta')
  })

  it('模型误写进正文的说明性注释被剥掉', () => {
    const html = renderMd('## 风险\n\n<!-- 无闭合标记，本节按卡片展示 -->\n\n这一节的内容')
    expect(html).toContain('这一节的内容')
    expect(html).not.toContain('无闭合标记')
  })

  it('行内注释（与文字同行）被剥掉', () => {
    expect(renderMd('前文 <!-- ZZCOMMENT2 --> 后文')).not.toContain('ZZCOMMENT2')
  })

  it('多行注释被剥掉', () => {
    expect(renderMd('前文\n\n<!--\nZZCOMMENT3\n-->\n\n后文')).not.toContain('ZZCOMMENT3')
  })

  it('⚠️ 代码块内的注释必须保留（那是用户本来就要展示的代码）', () => {
    const html = renderMd('```\n<!-- ZZCODE -->\n```')
    expect(html).toContain('ZZCODE')
    expect(html).toContain('<pre>')
  })

  it('⚠️ 行内代码里的注释必须保留', () => {
    expect(renderMd('`<!-- ZZINLINE -->`')).toContain('ZZINLINE')
  })

  it('纯文本插件集（网页/文本路径）同样生效', () => {
    const html = renderMd('前文\n\n<!-- ZZCOMMENT4 -->\n\n后文', REHYPE_PLUGINS_PLAIN)
    expect(html).toContain('后文')
    expect(html).not.toContain('ZZCOMMENT4')
  })

  it('用户正文里的合法 HTML 行不受影响（行为与改动前一致）', () => {
    // LiMo 从不渲染 HTML（无 rehype-raw）—— 合法标签同样是转义显示。
    // 这条钉住「剥注释」没有顺手改掉别的行为。
    expect(renderMd('<div>ZZDIV</div>')).toContain('ZZDIV')
  })

  // ── 对照实验：不挂插件时注释确实会漏出来 ──
  // 少了它，上面各条无法排除「这个 md 本来就不产生注释节点」的假绿。
  it('对照：移除插件后注释被转义成可见文本', () => {
    // REHYPE_PLUGINS[1] 是 KaTeX 项（不含 strip），即改动前的插件集
    const html = renderMd('<!-- ZZCOMMENT5 -->\n\n正文', [REHYPE_PLUGINS[1]])
    expect(html).toContain('ZZCOMMENT5')
  })
})
