# LiMo（沥墨）

> [github.com/lazybone404/LiMo](https://github.com/lazybone404/LiMo)

AI 知识管理系统。输入 URL、文件或文本 → 自动提取正文 → LLM 生成结构化 Markdown 笔记。

设计哲学：**复杂性不是设计进去的，而是后续插件化长出来的。** 核心路径极简，扩展通过插件堆叠。

## 使用方式

### 桌面应用（推荐）

下载 [Releases](https://github.com/lazybone404/LiMo/releases) 中的安装包：

- `LiMo_*_x64-setup.exe` — NSIS 安装包
- `LiMo_*_x64_en-US.msi` — MSI 安装包

支持 Windows 10+，开箱即用。首次启动会检测可选组件（ffmpeg 已内置，SearXNG 可选安装）。

### 开发环境

#### 前置要求

- Python 3.12+（通过 [uv](https://docs.astral.sh/uv/) 管理依赖）
- Node.js 18+（前端）
- Rust（仅桌面打包需要）

#### 一键启动

```bash
bash start.sh
```

后端 `7413` 端口，前端 `5173` 端口。浏览器打开 `http://localhost:5173`。

### 配置

首次启动后在设置页（顶部齿轮图标）填写 LLM API Key：

1. **模型 & 引擎** → 选择厂商（OpenAI / Anthropic / DeepSeek / DashScope 等），填写 Key
2. **代理** → 访问外网 API 需配置代理（如 `http://127.0.0.1:7890`）
3. **Agent 工具** → 网络搜索方案切换（Tavily / SearXNG / DuckDuckGo）

配置自动持久化到 `backend/config.yaml`。

## 支持的类型

| 类型 | 输入 | 处理方式 |
|------|------|---------|
| 网页 | URL | trafilatura 提取正文 |
| 视频 | B站/YouTube URL 或本地文件 | 字幕优先 → yt-dlp 下载 → ASR（DashScope / faster-whisper） |
| PDF | 电子/扫描版 | pymupdf4llm 全自动（文字+表格+嵌入图+矢量图）/ OCR |
| Word | .docx | python-docx / markitdown（文字+表格+图片） |
| PPT | .pptx | python-pptx / markitdown（文字+图片+备注） |
| Excel | .xlsx | openpyxl 表格提取 → 分析报告+数据摘要 |
| EPUB | .epub | ebooklib 章节+骨架提取，支持选中章节生成 |
| 图片 | 上传 | LLM 多模态优先 → Tesseract OCR 回退 |
| 文本 | 粘贴 | 直接存为原文 |

## 功能

**导入**：SSE 流式进度，URL 去重 + 内容哈希去重，文件拖拽上传，视频支持 B站/YouTube 字幕+平台认证 Cookie。

**生成**：双产物并行（整理版原文 + 结构化笔记），类型感知 prompt 模板，流式渲染，可取消（真正停止 token 消耗），取消后保留残缺产物可续写，自动续写（最长 3 轮），目标语言可配置（8 种）。

**图片处理**：4 种模式 — 文字提取 / 表格数据 / 文档数字化 / 内容理解，LLM 多模态优先 → 引擎回退，图片自动优化（PIL 缩放 ≤1024px）。

**事实验证**：三阶段管道 — 提取事实断言(LLM) → 逐条搜索验证(web) → 交叉核查(LLM) → 生成 Markdown 报告，SSE 流式进度。

**AI 对话**：Agent 工具循环，5 个工具（search_docs / lookup_doc / list_docs / search_web / fetch_url），搜索三级降级（Tavily → SearXNG → DuckDuckGo），对话持久化+CRUD。

**导出**：MD / 自包含 HTML（零 CDN·4 主题·KaTeX 服务端渲染·Pygments 代码高亮）/ ZIP（三种图片模式+多产物标签）/ 思维导图（SVG + markmap 交互式 HTML·暗色自适应）/ 批量多文档打包。

**可选组件**：ffmpeg 默认内置（视频下载+音频提取），SearXNG 运行时检测+首次启动引导安装，未安装自动降级。

## 架构

```
输入 → Adapter.adapt() → RawTranscript → 存储
                │
    ┌───────────┼───────────┐
    ▼           ▼           ▼
  LLM 生成    图片处理    AI 对话
  (Generator) (Process)  (AgentRunner)
    │           │           │
    ▼           ▼           ▼
MarkdownView ProcessResult Chat
```

- **7 个适配器**：Video（含 B站/YouTube 字幕优先提取）→ Document → Image → Webpage → Text（兜底）。`accept()` + `adapt()` 注册即用，注册顺序决定路由优先级
- **16 个引擎**：ASR 3（DashScope / faster-whisper-small / medium）/ Doc 9 / OCR 2（DashScope / Tesseract 便携版）
- **Agent**：5 个工具 + 搜索三级降级 + 40 条历史上下文
- **桌面壳**：Tauri v2（Rust）→ spawn PyInstaller 打包的 Python 后端 → WebView2 加载前端

详见 [CLAUDE.md](CLAUDE.md)（开发指南）和 [docs/](docs/)（设计文档）。

## 项目结构

```
LiMo/
├── start.sh                       # 一键启动后端+前端
├── backend/
│   ├── main.py                    # FastAPI 入口（7413 端口）
│   ├── config.yaml                # 配置（自动生成，不提交 git）
│   ├── pyproject.toml             # Python 依赖（uv）
│   ├── LiMoBackend.spec           # PyInstaller 打包配置
│   ├── static/                    # KaTeX + markmap/d3 离线资源
│   ├── app/
│   │   ├── adapters/              # 7 个适配器
│   │   ├── engines/               # 16 个引擎（ASR/Doc/OCR）
│   │   ├── generation/            # LLM 生成器 + 事实验证
│   │   ├── agent/                 # Agent 工具 + 循环
│   │   ├── llm/                   # LLM 统一调用层
│   │   ├── routers/               # 10 个路由模块
│   │   ├── storage/               # 文件系统存储 + JSON 索引
│   │   ├── downloaders/           # 抖音下载器
│   │   ├── utils/                 # 视频帧质量/水印/SSRF 防护
│   │   └── core/                  # 配置/类型/异常/日志/并发/模型解析
│   ├── bin/                       # 便携工具
│   │   ├── tesseract/             # Tesseract OCR 便携版
│   │   └── searxng/               # SearXNG Windows 便携版（可选）
│   └── tests/                     # 6 个测试
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── gen-panel/         # 生成面板（导入+选项+引擎选择）
│   │   │   ├── doc-library/       # 文档库（搜索+筛选+列表）
│   │   │   ├── preview/           # 预览区（Markdown+思维导图+TOC+骨架）
│   │   │   ├── ai-chat/           # AI 对话面板+浮窗
│   │   │   ├── settings/          # 设置页
│   │   │   └── ui/                # 通用 UI 组件
│   │   ├── api/                   # API 客户端 + SSE 流式 + 取消管理
│   │   ├── lib/                   # 工具函数（主题/下载/markmap/markdown）
│   │   └── store/                 # Zustand 状态管理
│   ├── src-tauri/                 # Tauri 桌面壳（Rust）
│   │   ├── src/lib.rs             # 后端进程管理 + 组件检测 + 就绪探测
│   │   ├── tauri.conf.json        # Tauri 配置（1400×900 + 最大化）
│   │   └── bin/LiMoBackend/       # PyInstaller 打包产物
│   ├── package.json
│   └── index.html
└── docs/                          # 设计文档 + 修改记录 + 状态快照
```

## 技术栈

| 层 | 技术 |
|------|------|
| 后端 | Python 3.12 · FastAPI · uvicorn · httpx · PyMuPDF · trafilatura · yt-dlp · OpenCV · Tesseract |
| LLM | OpenAI SDK · dashscope · tiktoken · pymupdf4llm · markitdown |
| 前端 | React 19 · TypeScript · Vite 8 · Tailwind v4 · Zustand · ReactMarkdown · KaTeX · markmap |
| 桌面 | Tauri v2 (Rust) · WebView2 · PyInstaller · NSIS/MSI |
| 存储 | 文件系统 + JSON 索引（`~/.mybigwork/`） |
| 导出 | Pygments · markdown · KaTeX 服务端渲染 · markmap 交互式 HTML |
