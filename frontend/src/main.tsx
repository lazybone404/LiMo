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
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
