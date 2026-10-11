import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'

/**
 * 将 LaTeX 分隔符转换为 remark-math 可识别的格式。
 *
 * remark-math 只识别 $ (inline) 和 $$ (display)，但学术论文常使用
 * \(...\) (inline) 和 \[...\] (display)。此函数预处理 Markdown 文本，
 * 将后者转换为前者，使公式能在 KaTeX 中正确渲染。
 *
 * 保护 fenced code blocks (```) 和 inline code (`) 内的内容不被修改。
 */
export function normalizeLatexDelimiters(md: string): string {
  if (!md || typeof md !== 'string') return md
  // 分割出代码块和普通文本，仅处理普通文本
  const parts = md.split(/(```[\s\S]*?```|`[^`\n]+`)/g)
  return parts.map((part, i) => {
    // 奇数索引 = 代码块内容，跳过
    if (i % 2 === 1) return part

    // \[...\] → $$...$$ (display math, 可能跨行)
    part = part.replace(/\\\[([\s\S]*?)\\\]/g, (_: string, latex: string) => {
      // 移除首尾多余空白，保留公式内部结构
      const trimmed = latex.trim()
      // 单行短公式 → 保持紧凑；多行或长公式 → 换行
      if (trimmed.includes('\n') || trimmed.length > 60) {
        return `$$\n${trimmed}\n$$`
      }
      return `$$${trimmed}$$`
    })

    // \(...\) → $...$ (inline math)
    part = part.replace(/\\\(([\s\S]*?)\\\)/g, (_: string, latex: string) => {
      return `$${latex.trim()}$`
    })

    // 含 \tag 的内联公式 → 提升为 display（KaTeX 的 \tag 仅 display 支持，
    // inline 渲染会报 "\tag works only in display equations" 显示红色 katex-error）
    part = part.replace(/\$([^$\n]+?\\tag\s*\{[^}]*\}[^$\n]*?)\$/g, (_: string, latex: string) => {
      return `$$\n${latex.trim()}\n$$`
    })

    return part
  }).join('')
}

/** 全文插件（含 KaTeX 数学公式） — 用于视频/文档等可能含公式的内容 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
// remarkMath 必须在 remarkGfm 之前：公式内的 _（下标）/<（x_{<k}）等字符
// 需先被 math 插件识别为公式语法，否则 GFM 会将 _ 转为斜体、< 解析为 HTML 标签
export const REMARK_PLUGINS: any[] = [remarkMath, remarkGfm]

/** 值可能承载 HTML 注释原文的节点类型（其余类型无 `value` 或语义不同） */
const COMMENT_NODE_TYPES = new Set(['raw', 'text', 'comment'])
/** 「整个值就是一个 HTML 注释」——不做文本中间替换，那会把 `前文  后文` 的空格改掉 */
const SOLE_COMMENT_RE = /^<!--[\s\S]*?-->$/

/**
 * 剥掉正文里的 HTML 注释（`<!-- … -->`）。
 *
 * ⚠️ **为什么需要它**：react-markdown 在**不启用** `allowDangerousHtml` / `rehype-raw`
 * 时是「HTML 不当 HTML、当文本转义输出」—— 注释于是**原样显示成一行字**（实测渲染
 * 结果是 `&lt;!-- … --&gt;`）。产物首行的 `<!-- limo-meta {…} -->` 与模型写进正文的
 * `<!-- 无闭合标记，本节按卡片展示 -->` 都属于这一类。
 * ⚠️ `skipHtml` **不是解法**：它删的是**所有** HTML，用户正文里的合法 HTML 会被一并删掉。
 *
 * ⚠️ **实测形态**（本插件的判据全建立在这两条上）：
 *   · 正文里的注释一律是 **`raw` 节点**（覆盖块级 / 句中 / 句末 / 多行四种位置），值即
 *     注释原文；句中注释会把前后文字切成各自独立的 `text` 节点，故整节点删除是安全的。
 *   · **代码块与行内代码里的注释是 `text` 节点**，值与正文注释**长得几乎一样**
 *     （`"<!-- … -->\n"` / `"<!-- … -->"`）—— 但那是用户**本来就要展示**的内容。
 *     故必须显式跳过 `code` / `pre` 子树：不跳就会静默删掉用户的代码。
 *   ⚠️ 判据对 `raw` / `text` / `comment` 三种类型都匹配（而非只认实测到的 `raw`）——
 *     落在「值是不是注释」这个语义上，不依赖 react-markdown 把注释归进哪一类。
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function rehypeStripComments() {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return (tree: any) => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const walk = (node: any) => {
      if (!Array.isArray(node?.children)) return
      // 倒序遍历：splice 不会跳过后续项
      for (let i = node.children.length - 1; i >= 0; i--) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const c = node.children[i] as any
        if (COMMENT_NODE_TYPES.has(c?.type) && typeof c.value === 'string'
            && SOLE_COMMENT_RE.test(c.value.trim())) {
          node.children.splice(i, 1)
          continue
        }
        if (c?.type === 'element' && (c.tagName === 'code' || c.tagName === 'pre')) continue
        walk(c)
      }
    }
    walk(tree)
  }
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const REHYPE_PLUGINS: any[] = [rehypeStripComments, [rehypeKatex, {
  strict: false,
  throwOnError: false,
  errorColor: 'var(--color-text)',
}]]

/** 纯文本插件（无 KaTeX） — 用于网页、纯文本等无公式内容
 *
 *  网页内容常混入 base64/CSS 残留，KaTeX 会误识别为公式并刷屏报错。
 *  此类内容不需要公式渲染，直接跳过 remarkMath + rehypeKatex。
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const REMARK_PLUGINS_PLAIN: any[] = [remarkGfm]
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const REHYPE_PLUGINS_PLAIN: any[] = [rehypeStripComments]
