/**
 * KaTeX 服务端预渲染 — 将 Markdown 中的 $$...$$ 和 $...$ 公式渲染为 HTML
 * 用法: node render_math.js < input.md > output.md
 * Python 调用: subprocess.run(["node", "scripts/render_math.js"], input=md, capture_output=True)
 */
const katex = require('../../frontend/node_modules/katex');
const readline = require('readline');

/**
 * 将 LaTeX 分隔符归一化为 remark-math 兼容格式。
 * \[...\] → $$...$$, \(...\) → $...$
 * 跳过 fenced code blocks (```) 和 inline code (`) 内的内容。
 */
function normalizeLatexDelimiters(md) {
  if (typeof md !== 'string') return md;
  const parts = md.split(/(```[\s\S]*?```|`[^`\n]+`)/g);
  return parts.map((part, i) => {
    if (i % 2 === 1) return part;  // code block, skip
    part = part.replace(/\\\[([\s\S]*?)\\\]/g, (_, latex) => {
      const trimmed = latex.trim();
      if (trimmed.includes('\n') || trimmed.length > 60) {
        return '$$\n' + trimmed + '\n$$';
      }
      return '$$' + trimmed + '$$';
    });
    part = part.replace(/\\\(([\s\S]*?)\\\)/g, (_, latex) => '$' + latex.trim() + '$');
    // 含 \tag 的内联公式 → 提升为 display（KaTeX 的 \tag 仅 display 支持）
    part = part.replace(/\$([^$\n]+?\\tag\s*\{[^}]*\}[^$\n]*?)\$/g, (_, latex) => '$$\n' + latex.trim() + '\n$$');
    return part;
  }).join('');
}

let input = '';
const rl = readline.createInterface({ input: process.stdin, terminal: false });
rl.on('line', (line) => input += line + '\n');
rl.on('close', () => {
  // 先归一化 LaTeX 分隔符（支持 \[...\] / \(...\)）
  let result = normalizeLatexDelimiters(input);

  // 渲染 $$...$$ 块级公式（含换行）
  result = result.replace(/\$\$\s*([\s\S]+?)\s*\$\$/g, (_, latex) => {
    try {
      return katex.renderToString(latex.trim(), {
        displayMode: true,
        throwOnError: false,
        strict: false,
        trust: true,
      });
    } catch (e) {
      return `[公式渲染失败: ${e.message}]`;
    }
  });

  // 渲染 $...$ 行内公式（不跨行）
  result = result.replace(/\$([^$\n]+?)\$/g, (_, latex) => {
    try {
      return katex.renderToString(latex.trim(), {
        displayMode: false,
        throwOnError: false,
        strict: false,
        trust: true,
      });
    } catch (e) {
      return `[公式渲染失败: ${e.message}]`;
    }
  });

  process.stdout.write(result);
});
