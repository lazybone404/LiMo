/**
 * KaTeX 服务端预渲染 — 将 Markdown 中的 $$...$$ 和 $...$ 公式渲染为 HTML
 * 用法: node render_math.js < input.md > output.md
 * Python 调用: subprocess.run(["node", "scripts/render_math.js"], input=md, capture_output=True)
 */
const katex = require('../../frontend/node_modules/katex');
const readline = require('readline');

let input = '';
const rl = readline.createInterface({ input: process.stdin, terminal: false });
rl.on('line', (line) => input += line + '\n');
rl.on('close', () => {
  let result = input;

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
