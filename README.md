# LiMo（沥墨）

> [github.com/lazybone404/LiMo](https://github.com/lazybone404/LiMo)

AI 知识管理系统。输入 URL、文件或文本 → 自动提取正文 → LLM 生成结构化 Markdown 笔记。

设计哲学：**复杂性不是设计进去的，而是后续插件化长出来的。** 核心路径极简，扩展通过插件堆叠。

## 快速开始

### 前置要求

- Python 3.12+（通过 [uv](https://docs.astral.sh/uv/) 管理依赖）
- Node.js 18+（前端）

### 一键启动

```bash
bash start.sh
```

后端 `7413` 端口，前端 `5173` 端口。浏览器打开 `http://localhost:5173`。

### 安装 SearXNG（可选，高质量搜索）

```bash
cd backend/bin/searxng/searxng
"SearXNG for Windows.exe"  # 或运行 .bat
```

不启动时，AI 对话的搜索功能自动降级 DuckDuckGo。

### 配置

首次启动后在设置页（顶部齿轮图标）填写 LLM API Key：

1. **模型 & 引擎** → DeepSeek / OpenAI / Anthropic 选一家填 Key
2. **代理** → 访问外网 API 需要 Clash 代理 `http://127.0.0.1:7890`
3. **Agent 工具** → 网络搜索方案切换（SearXNG / DuckDuckGo）

配置自动持久化到 `backend/config.yaml`，API Key 已加入 `.gitignore`。

## 支持的类型

| 类型 | 输入 | 处理方式 |
|------|------|---------|
| 网页 | URL | trafilatura 提取正文 |
| 视频 | B站/YouTube URL 或本地文件 | 字幕优先 → yt-dlp 下载 → ASR |
| PDF | 电子/扫描版 | pymupdf4llm 全自动·文字+表格+嵌入图+矢量图 / OCR |
| Word | .docx | markitdown 微软官方转换（文字+表格+图片） |
| PPT | .pptx | markitdown 微软官方转换（文字+图片+备注） |
| Excel | .xlsx | openpyxl 表格提取 → 分析报告+数据摘要 |
| EPUB | .epub | ebooklib 章节+骨架提取 |
| 图片 | 上传 | LLM 多模态优先 → Tesseract OCR 回退 |
| 文本 | 粘贴 .md/.txt | 直接存为原文 |

## 功能

**导入**：SSE 流式进度，URL 去重 + 内容哈希去重，视频支持 B站/YouTube 字幕。

**生成**：双产物并行（整理版原文 + 结构化笔记），实时流式渲染，可取消（真正停止 token 消耗），取消后保留残缺产物可续写。

**图片处理**：提取文字 / 表格数据 / 文档数字化 / 理解内容，LLM 多模态优先 → 引擎回退，支持降级通知。

**AI 对话**：Agent 工具循环，查询本地文档 + 搜索网络 + 抓取网页，最多 3 轮。

**导出**：MD / 自包含 HTML（零 CDN·4 主题）/ ZIP（三种图片模式：独立文件夹·base64 内嵌·不包含）/ 思维导图（SVG + 交互式 markmap HTML·暗色自适应）/ 批量多文档打包。

**多任务**：全局并发控制（最多 3 个），任务面板可拖拽查看和取消。

## 架构

```
输入 → Adapter.adapt() → RawTranscript
                ↓
    ┌───────────┼───────────┐
    ▼           ▼           ▼
  LLM 生成    LLM 处理    LLM 对话
  (Generator) (Process)    (AgentRunner)
    │           │           │
    ▼           ▼           ▼
  MarkdownView  ProcessResult   Chat
```

- **适配器**：Video / Webpage / Document / Image / Text，`accept()` + `adapt()` 注册即用
- **引擎**：ASR（DashScope / faster-whisper）/ Doc（PyMuPDF / Docx / Pptx / Excel / EPUB）/ OCR（DashScope / Tesseract）
- **Agent**：search_web（SearXNG→DDG）+ fetch_url（trafilatura/Jina），配置可切换

详见 [CLAUDE.md](CLAUDE.md) 和 [docs/](docs/)。

## 项目结构

```
LiMo/
├── start.sh                    # 一键启动后端+前端
├── backend/
│   ├── main.py                 # FastAPI 入口
│   ├── config.yaml             # 配置（自动生成，不提交 git）
│   ├── app/
│   │   ├── adapters/           # 5 个适配器
│   │   ├── engines/            # 16 个引擎
│   │   ├── generation/         # LLM 生成器
│   │   ├── agent/              # Agent 工具 + 循环
│   │   ├── llm/                # LLM 统一调用层
│   │   ├── routers/            # 9 个路由模块
│   │   ├── storage/            # 文件系统存储
│   │   └── core/               # 配置/类型/异常/日志
│   └── bin/
│       ├── tesseract/          # Tesseract 便携版
│       └── searxng/            # SearXNG Windows 版
├── frontend/
│   └── src/
│       ├── components/         # 生成面板/文档库/预览区/AI面板/设置
│       ├── api/                # API 客户端 + SSE 流式
│       └── store/              # Zustand 状态管理
└── docs/                       # 设计文档 + 状态快照
```

## 技术栈

| 层 | 技术 |
|------|------|
| 后端 | Python 3.12 · FastAPI · uvicorn · AsyncOpenAI · yt-dlp · PyMuPDF · trafilatura · Tesseract |
| 前端 | React 19 · TypeScript · Vite 8 · Tailwind v4 · Zustand · ReactMarkdown · KaTeX |
| 存储 | 文件系统 + JSON 索引，`~/.mybigwork/` |
