import { describe, it, expect } from 'vitest'
import { normalizeLatexDelimiters } from '../lib/markdown-plugins'

describe('normalizeLatexDelimiters', () => {
  const $$ = (s: string) => `$${s}$`

  it('converts \\(inline\\) to $inline$', () => {
    const input = 'Formula: \\(E=mc^2\\) in text.'
    const result = normalizeLatexDelimiters(input)
    expect(result).toBe(`Formula: ${$$('E=mc^2')} in text.`)
  })

  it('converts \\[display\\] to $$display$$', () => {
    const input = '\\[L = \\frac{1}{2} \\rho v^2 S C_L\\]'
    const result = normalizeLatexDelimiters(input)
    // input 含单反斜杠 \frac，输出保留单反斜杠
    expect(result).toBe('$$L = \\frac{1}{2} \\rho v^2 S C_L$$')
  })

  it('converts multiline \\[...\\] to $$\\n...\\n$$', () => {
    const input = '\\[\nL = \\frac{1}{2} \\rho v^2 S C_L\n\\]'
    const result = normalizeLatexDelimiters(input)
    expect(result).toContain('$$')
    expect(result).toContain('\\frac')
    expect(result).not.toContain('\\[')
  })

  it('preserves content inside fenced code blocks', () => {
    const input = 'Before.\n```python\nx = \\[1, 2, 3\\]\n```\nAfter.'
    const result = normalizeLatexDelimiters(input)
    expect(result).toContain('\\[1, 2, 3\\]')
    expect(result).toContain('```')
  })

  it('preserves content inside inline code', () => {
    const input = 'The regex `\\[pattern\\]` should match.'
    const result = normalizeLatexDelimiters(input)
    expect(result).toContain('`\\[pattern\\]`')
  })

  it('preserves \\tag and \\text commands in formulas', () => {
    const input = '\\[L = \\frac{T_{\\text{draft}} + T_{\\text{verify}}}{\\tau}. \\tag{1}\\]'
    const result = normalizeLatexDelimiters(input)
    expect(result).toContain('\\text')
    expect(result).toContain('\\tag{1}')
    expect(result).toContain('$$')
  })

  it('preserves \\mathbb, \\mathcal, \\mathbf commands', () => {
    const input = '\\[W_c \\in \\mathbb{R}^{d \\times m d}\\]'
    const result = normalizeLatexDelimiters(input)
    expect(result).toContain('\\mathbb')
    expect(result).toContain('$$')
  })

  it('preserves \\odot and \\sigma', () => {
    const input = '\\[\\sigma(W_g z_k) \\odot s_{k-1}\\]'
    const result = normalizeLatexDelimiters(input)
    expect(result).toContain('\\odot')
    expect(result).toContain('\\sigma')
    expect(result).not.toContain('\\[')
  })

  it('handles empty input', () => {
    expect(normalizeLatexDelimiters('')).toBe('')
    expect(normalizeLatexDelimiters(null as any)).toBe(null)
  })

  it('converts mixed inline and display in the same text', () => {
    const input = 'Inline \\(x^2\\) and display \\[\\sum_{i=1}^n i\\] both work.'
    const result = normalizeLatexDelimiters(input)
    expect(result).toContain('$x^2$')
    expect(result).toContain('$$')
    expect(result).not.toContain('\\(')
    expect(result).not.toContain('\\[')
  })

  it('handles formulas with < subscripts (paper pattern)', () => {
    const input = '\\[p_k(v|x_0,x_{<k}) = \\frac{\\exp}{\\sum}\\]'
    const result = normalizeLatexDelimiters(input)
    expect(result).toContain('{<k}')
    expect(result).toContain('$$')
  })
})
