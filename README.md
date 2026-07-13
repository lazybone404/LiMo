# LiMo（沥墨）

> 把任何内容变成可读、可搜索的结构化笔记。

粘贴链接或拖入文件，AI 自动提取正文、转写语音、生成结构化 Markdown 笔记。支持网页、视频、PDF/Word/PPT/Excel/EPUB 文档和图片。

---

## 功能

### 📥 内容导入

**五种输入方式**，适配不同类型的内容来源：

| 类型 | 说明 | 支持格式 |
|------|------|---------|
| **网页** | 提取正文，去掉广告/导航/侧边栏 | 任意 HTTP/HTTPS URL |
| **视频** | 语音转文字 + 字幕提取 | B 站、YouTube、本地视频文件 (mp4/mkv 等) |
| **文件** | 文档转录为 Markdown | PDF、Word (.docx)、PPT (.pptx)、Excel (.xlsx)、EPUB |
| **图片** | OCR 文字识别 | PNG、JPG、WebP、BMP、TIFF |
| **文本** | 直接粘贴或输入 | 纯文本、Markdown |

**拖拽导入**：直接把文件拖到窗口即可。

**导入即转录**：文件导入时自动提取正文——PDF 用 pymupdf4llm（文字+图片+表格），Word/PPT 用 python-docx/pptx，Excel 用 openpyxl，EPUB 用 ebooklib。视频导入自动语音转写，B 站/YouTube 优先提取官方字幕。

### 🎙️ 转录引擎

内置 **17 个引擎**，覆盖语音转写、文档提取、文字识别三类，开箱即用：

| 类别 | 引擎 | 说明 |
|------|------|------|
| **ASR** | bcut-asr | B 站必剪免费云转写，中文优化，无需 Key |
| | dashscope-asr | 阿里云 Paraformer，大文件语音转文字 |
| | faster-whisper | 本地模型（small/medium/large-v3），离线可用 |
| **Doc** | pymupdf4llm | PDF 推荐引擎，文字+图片+矢量图全自动 |
| | pymupdf / pymupdf-text | PDF 文字层直读 / 仅文字 |
| | pdf-ocr | 扫描件 PDF OCR |
| | python-docx / python-pptx / openpyxl | Office 文档 |
| | markitdown | 通用文档转换兜底 |
| | ebooklib | EPUB 章节+骨架提取 |
| **OCR** | dashscope-ocr | 阿里云 VL 模型，文字提取 |
| | tesseract | 本地 OCR，离线可用 |

引擎可在设置中独立配置，支持优先级链（主引擎失败自动降级到备选）。每种文件类型可绑定不同引擎，切换后点「重新转录」即可。

### ✨ AI 生成笔记

转录完成后，AI 基于正文生成结构化笔记。支持 **8 种目标语言**输出。

**双产物并行**：

| 产物 | 说明 |
|------|------|
| **笔记 (notes)** | 结构化提取关键信息，支持目录/时间戳/AI 总结/公式集/参考文献等格式选项 |
| **整理版原文 (polished)** | 去噪音、修正错别字、合并段落，适合存档和引用 |

**不同内容类型的处理**：
- **视频**：可选「AI 画面理解」——自动抽帧+网格拼图发给大模型分析画面内容（PPT 演示、代码演示等）
- **文档**：可选保留原文图片引用 + AI 分析图片内容
- **网页/文本**：通用笔记生成
- **Excel**：生成数据摘要（概况、关键指标、字段解读）

**生成选项**：
- 风格选择（详细/简洁/商务/学术/生活等）
- 可选格式：目录、原片链接、原片截图、AI 总结、翻译、公式集、参考文献、文档图片
- 流式输出，实时看到生成进度
- 支持取消和续写（取消后可从断点继续）
- 同模式保留最近 2 版，可切换 v1/v2

### 💬 AI 对话

选中文档后，和 AI 聊它的内容。AI Agent 拥有 **5 个工具**：

| 工具 | 说明 |
|------|------|
| search_docs | 搜索你的文档库 |
| lookup_doc | 查看指定文档详情 |
| list_docs | 列出所有文档 |
| search_web | 网络搜索（Tavily → SearXNG → DuckDuckGo 三级降级） |
| fetch_url | 抓取网页内容 |

支持多轮对话、对话持久化、流式输出。内置 SearXNG 便携版（自动启动，无需额外安装）。

### ✅ 事实验证

一键核查笔记中的事实是否准确。**三阶段流程**：

1. **提取**：LLM 从笔记中提取所有事实断言
2. **验证**：逐条搜索网络证据，比对真伪
3. **报告**：LLM 交叉核查，生成结构化验证报告（通过/争议/无法验证三类，附置信度评分）

验证源自动选择：优先用已有笔记，其次整理版原文，最后原始转录。

### 📤 导出

**快捷导出**（预览区工具栏）和**批量导出**（顶部 Download 面板）两条路径。

| 格式 | 说明 |
|------|------|
| **Markdown** | 单文件下载，图片路径自动转绝对 URL，外部查看不裂图 |
| **HTML** | 完全自包含（KaTeX 公式+代码高亮+GFM），4 种主题，断网可用 |
| **ZIP** | 打包全部产物，图片可独立文件夹/base64 内嵌/不包含 |
| **思维导图** | 交互式 HTML（markmap，可展开/收起）或 SVG 矢量图 |
| **对话导出** | 对话记录导出为 Markdown + JSON |

---

## 安装

### 桌面应用（推荐）

从 [Releases](https://github.com/lazybone404/LiMo/releases) 下载安装包（NSIS 374MB / MSI 514MB），双击安装。支持 Windows 10+。

内置 ffmpeg（434MB）、SearXNG、Tesseract OCR、KaTeX 字体——开箱即用，无需装 Python 或任何依赖。

### 开发环境

需要 Python 3.12+、Node.js 18+。

```bash
bash start.sh       # 一键启动后端(7413) + 前端(5173)
bash restart-backend.sh  # 重启后端
```

浏览器打开 `http://localhost:5173`。

---

## 首次使用

1. 打开设置（顶部齿轮图标）
2. 在「模型 & 引擎」→「LLM 厂商」中添加 LLM 厂商，填写 API Key
3. 导入一篇内容试试

语音转写和文档转录引擎是内置的（bcut ASR、pymupdf4llm、Tesseract OCR 等），不需要额外配置。搜索引擎首次使用时自动启动 SearXNG。

---

## 技术架构

```
输入 → 适配器提取正文 → 存储 → LLM 生成笔记 / 图片处理 / AI 对话 / 事实验证 → 导出
```

- **后端**：Python FastAPI，7 个适配器，17 个引擎，11 个路由模块，~60 个 API 端点
- **前端**：React + TypeScript + Tailwind v4，四栏布局可拖拽（50-600px），流式 SSE 渲染
- **桌面壳**：Tauri v2（Rust）套 WebView2，PyInstaller 打包后端
- **存储**：文件系统 + JSON 索引，数据目录 `~/.mybigwork/`
- **安全**：SSRF 防护（14 个内网 IP 段）、路径遍历防护、XSS 防护、敏感信息脱敏

详细文档见 [CLAUDE.md](CLAUDE.md) 和 [docs/](docs/)。
