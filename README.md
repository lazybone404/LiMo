# LiMo（沥墨）

> 把任何内容变成可读、可搜索的结构化笔记。

粘贴一个链接，得到一篇 Markdown。支持网页文章、B 站/YouTube 视频、PDF/Word/PPT/Excel/EPUB 文档，以及图片 OCR。

---

## 能做什么

### 导入 & 转录

把信息放进 LiMo——支持 URL、本地文件、纯文本粘贴。导入时自动：

- **网页** → 提取正文，去掉广告和导航
- **视频** → 提取字幕或语音转文字（AI 云转写 / 本地模型）
- **PDF/Office/EPUB** → 提取文字、表格和图片
- **图片** → OCR 文字识别

拖拽文件到窗口就能导入，支持 2GB 大文件。

### AI 生成笔记

选好风格，点「生成」——AI 同时产出两份内容：

- **笔记**：结构化提取关键信息，带目录、时间戳、AI 总结
- **整理版原文**：去噪、纠错、合并段落，更干净的原文

视频笔记支持插入**原片截图**（AI 标记关键画面 → 自动抽帧嵌入），以及 **AI 画面理解**（让大模型看着画面理解内容，比如 PPT 演示、代码演示）。

### AI 对话

选中一篇文档，直接和 AI 聊它的内容。AI 能搜索你的文档库、查网络资料、打开链接——像一个能上网的研究助手。

### 事实验证

一键检查笔记中的事实是否准确。AI 会提取所有关键主张、上网搜索证据、交叉比对，生成验证报告。

### 导出

- **Markdown**：一键下载，适合放到 Obsidian/Notion
- **HTML**：自包含单文件，KaTeX 公式渲染、代码高亮、4 种主题，断网也能看
- **ZIP**：打包全部产物（笔记 + 原文 + 事实验证 + 截图），图片可独立文件夹或内嵌
- **思维导图**：交互式 HTML（可点击展开/收起）或 SVG 矢量图

---

## 安装

### 桌面应用（推荐）

从 [Releases](https://github.com/lazybone404/LiMo/releases) 下载安装包，双击安装。支持 Windows 10+，开箱即用，无需装 Python。

### 开发环境

需要 Python 3.12+、Node.js 18+。

```bash
bash start.sh
```

浏览器打开 `http://localhost:5173`。

---

## 首次使用

1. 打开设置（顶部齿轮图标）
2. 在「模型 & 引擎」中添加 LLM 厂商（DeepSeek / OpenAI / DashScope 等），填写 API Key
3. 导入一篇内容试试

语音转写和文档转录的引擎是内置的，不需要额外配置。

---

## 技术架构

```
输入 → 适配器提取正文 → 存储 → LLM 生成笔记 / 图片处理 / AI 对话 → 导出
```

- **后端**：Python FastAPI，7 个适配器（Video / Document / Image / Webpage / Text 等），17 个引擎（ASR / Doc / OCR）
- **前端**：React + TypeScript + Tailwind，四栏布局可拖拽，流式渲染
- **桌面壳**：Tauri v2（Rust）套 WebView2，PyInstaller 打包后端
- **存储**：文件系统 + JSON 索引，`~/.mybigwork/` 目录

详细开发文档见 [CLAUDE.md](CLAUDE.md) 和 [docs/](docs/)。

---

## 项目结构

```
LiMo/
├── backend/app/
│   ├── adapters/     # 内容适配器
│   ├── engines/      # 转录引擎（ASR/Doc/OCR）
│   ├── generation/   # LLM 生成 + 事实验证
│   ├── agent/        # AI Agent 工具
│   ├── llm/          # LLM 统一调用层
│   ├── routers/      # API 路由
│   ├── storage/      # 文件存储 + 索引
│   └── core/         # 配置/异常/日志/安全
├── frontend/src/
│   ├── components/   # UI 组件
│   ├── api/          # API 客户端
│   ├── lib/          # 工具函数
│   └── store/        # 状态管理
└── docs/             # 设计文档
```
