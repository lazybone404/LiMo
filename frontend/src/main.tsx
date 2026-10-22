import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import 'github-markdown-css/github-markdown-light.css'
import './index.css'
// ⚠️ 必须在 index.css **之后**：卡片规则靠特异性取胜（0,2,1 压过 vendor 的 0,1,1），
// 但放最后是纵深防御 —— 顺序问题不会报错，只会让某条规则静默失效。
// ⚠️ 这份文件**同时是后端导出的来源**（`export.py::_load_card_css()` 从磁盘读它内联进
// 导出 HTML），故两个 import 位置都不能改成「只在某个组件里按需加载」。
import './styles/cards.css'
// ⚠️ 同样**同时是后端导出的来源**（`export.py::_load_block_css()`）。与 cards.css 分家：
// 那份管骨架（章节怎么组织），这份管标记（一段内容是什么性质）—— 两者正交。
import './styles/blocks.css'
// ⚠️ 对话侧的两个卡片块形态（`ai-chat/ChatMarkerContent.tsx`）。与上两份**不同**：
// 这份**不进导出**（`export.py` 不读它）—— 对话没有 HTML 导出形态，故可自由引用 `--tk-*`。
import './styles/chat-blocks.css'
import App from './App.tsx'

/**
 * 静态演示站（`npm run build:demo`，见 `VITE_DEMO`）—— 在挂载 React **之前**装上假后端。
 *
 * ⚠️ 顺序不能颠倒：`App` 的启动链在首次 effect 里就发请求（health / capabilities /
 * transcripts / folders），晚一步会先打到不存在的后端上，页面上表现为「后端离线」横幅
 * 闪一下再消失。
 * 正式构建里 `import.meta.env.VITE_DEMO` 是编译期常量，Rollup 会把整个分支连同
 * `./demo/*` 一起摇掉 —— 演示数据不进正式包。
 */
async function bootstrap() {
  if (import.meta.env.VITE_DEMO === '1') {
    const { installDemoFetch } = await import('./demo/mock')
    installDemoFetch()
  }
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
}

void bootstrap()
