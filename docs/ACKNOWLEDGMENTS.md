# 开源致谢（Open Source Acknowledgments）

> **尊重声明**：LiMo 站在开源巨人的肩膀上。我们吸收了大量优秀开源项目的**精华与经验**——从设计思想、引擎能力到工程实践。这份文件记录这些贡献，表达我们最诚挚的敬意与感谢。
>
> 我们承诺：不剥离任何版权声明，遵守各许可证条款，AGPL/GPL 类依赖一律进程外隔离，许可证文本随发行物附带。

---

## 一、设计借鉴与致敬（Design Inspiration）

这些项目塑造了 LiMo 的核心设计语言，是我们的**精神标杆**：

| 项目 | 许可证 | 借鉴点 |
|------|--------|--------|
| **BiliNote**（哔哩哔哩开源 AI 笔记工具） | — | 视频「AI 画面理解」一个开关 = VL 管线 + 网格图发 LLM（网格图不嵌入笔记）、双产物并行、笔记风格体系——LiMo 视频笔记体验的直接对标 |
| **bcut / 必剪** | — | B 站免费云转写接口（bcut-asr 引擎）——让本地知识管理也能白嫖高质量 ASR |
| **SearXNG** | [AGPL-3.0](https://github.com/searxng/searxng) | 对话搜索降级链的元搜索引擎（Tavily → SearXNG → DuckDuckGo 三级降级），便携版进程外启动 |
| **diagram-design**（原仓库） | — | 图表视觉规范（背景/描边/配色/节点样式/正常显示硬规则），LiMo 图表生成的样式基石 |
| **markitdown**（微软） | [MIT](https://github.com/microsoft/markitdown) | Office/EPUB 统一转 Markdown 的适配器思路，LiMo 文档管线的骨干 |
| **resvg** | [MPL-2.0](https://github.com/linebender/resvg) | SVG 渲染的探索与实测经验——虽因 Windows 文字 bug 切换 PIL 自绘，但其路径探索沉淀在代码注释与 CHANGELOG 中 |

---

## 二、第三方依赖 — 后端（Python）

| 项目 | 许可证 | 用途 |
|------|--------|------|
| FastAPI / Starlette | MIT / BSD-3 | Web 框架 |
| uvicorn | BSD-3 | ASGI 服务器 |
| Pydantic | MIT | 数据校验 |
| httpx / requests / aiohttp | BSD-3 / Apache-2.0 / Apache-2.0 | HTTP 客户端 |
| openai | Apache-2.0 | LLM 统一调用（流式/取消/错误处理） |
| tiktoken | MIT | token 计数与上下文预算 |
| dashscope（阿里） | Apache-2.0 | 千问 OCR/ASR/视频理解原生通道 |
| PyYAML | MIT | 配置 |
| pydantic / python-dateutil / python-multipart | MIT / BSD-3 / Apache-2.0 | 基础工具 |
| markitdown（微软） | MIT | Office/EPUB → Markdown |
| python-docx / python-pptx / openpyxl | MIT ×3 | Word/PPT/Excel 解析 |
| epub-container | 自研（纯标准库） | EPUB 章节/图片/元数据提取（2026-08-26 起替代 ebooklib） |
| pdfplumber | MIT | PDF 表格提取 |
| pypdfium2 | BSD-3-Clause / Apache-2.0 | PDF 文字直读 + 原图嵌入 |
| xlrd | BSD-3 | 旧版 Excel (.xls) |
| beautifulsoup4 | MIT | HTML 解析（Word 导出用；2026-09-21 由传递依赖提为显式） |
| magika | Apache-2.0 | 文件类型检测（经 markitdown 引入，PyInstaller 需连带保留 onnxruntime） |
| Pillow | MIT-CMU（HPND） | 图片处理/图表渲染 |
| numpy | BSD-3 | 数值计算/模糊帧过滤 |
| trafilatura | Apache-2.0 | 网页正文提取 |
| yt-dlp | Unlicense | 视频/网页下载 |
| ffmpeg（gyan.dev essentials 构建） | **GPLv3**（[gyan.dev 官方](https://www.gyan.dev/ffmpeg/builds/)声明 "All builds are ... licensed as GPLv3"） | 视频抽帧/处理（独立进程调用，无传染；全文见 `licenses/GPL-3.0.txt`） |
| ffmpeg-python | Apache-2.0 | ffmpeg Python 绑定 |
| youtube-transcript-api | MIT | YouTube 字幕提取 |
| Pygments | BSD-2 | 代码高亮 |
| python-markdown | BSD-3 | Markdown 渲染 |
| duckduckgo-search | MIT | 搜索降级链末端 |
| certifi | MPL-2.0 | SSL 证书（PyInstaller 打包） |
| pytest / PyInstaller | MIT / GPL-2.0 (bootloader 例外) | 测试 / 打包 |

---

## 三、第三方依赖 — 前端（TypeScript/React）

| 项目 | 许可证 | 用途 |
|------|--------|------|
| React / ReactDOM | MIT | UI 框架 |
| react-markdown + remark-gfm/math + rehype-katex/slug | MIT | Markdown 渲染管线（GFM/公式/锚点） |
| KaTeX | MIT | 数学公式排版 |
| markmap-lib/view/common | MIT | 思维导图（SVG + 交互式 HTML） |
| Zustand | MIT | 状态管理（7 slices 领域拆分） |
| Tailwind CSS | MIT | 样式系统 |
| github-markdown-css | MIT | Markdown 基础样式（经 `--tk-*` 变量覆盖） |
| lucide-react | ISC | 图标 |
| react-syntax-highlighter + highlight.js | MIT / BSD-3 | 代码高亮 |
| react-medium-image-zoom | MIT | 图片点击放大 |
| Vite / Vitest | MIT | 构建 / 测试 |
| TypeScript | Apache-2.0 | 类型系统 |
| oxlint | MIT | 代码检查 |
| jsdom / @testing-library | MIT | 测试环境 |
| @tauri-apps/api / cli | MIT OR Apache-2.0 | Tauri 集成 |

---

## 四、第三方依赖 — 桌面壳（Tauri / Rust）

| 项目 | 许可证 | 用途 |
|------|--------|------|
| Tauri | MIT OR Apache-2.0 | 桌面应用框架（WebView2 + Rust 壳） |
| tauri-plugin-log / shell | MIT OR Apache-2.0 | 日志 / 进程管理 |
| serde / serde_json / log | MIT OR Apache-2.0 | 序列化 / 日志 |

---

## 五、曾集成、现已移除或插件化的项目

LiMo 的「口子原则」要求重依赖、AGPL/协议风险项目**进程外插件化**——它们仍在服务，但以更合规、更轻量的方式：

| 项目 | 许可证 | 状态 | 留下的经验 |
|------|--------|------|-----------|
| faster-whisper | MIT | 已从内置移除 | 本地转写能力，用户可自行安装插件 |
| Tesseract | Apache-2.0 | 已从内置移除 | 本地 OCR 插件模板（`examples/tesseract_plugin.py`） |
| docling | MIT | 已移除 | torch ~2GB 无法打包的教训 → 轻量引擎优先 |
| pymupdf4llm | AGPL-3.0 | **进程外插件**（`~/.limo/plugins/`） | AGPL 隔离模式，官方只维护契约 + 加载 + 文档 |
| resvg / resvg-py | MPL-2.0（核心）/ MIT（绑定层） | 已切换 PIL 自绘 | Windows 无法渲染 SVG 文字（上游普遍 bug）→ 自研子集渲染器。`pyproject.toml` 里的依赖声明已于 **2026-10-06 清理**（自 2026-08-16 起零 import） |
| SearXNG | AGPL-3.0 | 便携版进程外 | 搜索降级链 + AGPL 进程外启动 |
| html2text | GPL-3.0 | 已移除（2026-08-26，webpage.py 改用 bs4 get_text） | GPL 库进主 exe 是「合并作品」→ 替换优于附全文 |
| ebooklib | AGPL-3.0 | 已移除（2026-08-26，自研 epub-container 纯标准库替代） | AGPL 库进主 exe 是风险点 → 自研零依赖解析器，达成主 exe 零 AGPL/GPL |
| abogus（抖音 `a_bogus` 签名） | GPL-3.0（**文件头自行声明**，源自 TikTokDownloader） | 已移除（2026-10-06，自研抖音下载路径整体删除，抖音改走 yt-dlp 通用路径） | 「头部自称 GPLv3 却被 import 进主 exe」与对外声明的「主 exe 零 GPL」正面冲突——自研平台协议路径的合规风险，高于它省下的那点下载流量（yt-dlp 要下整段视频，但那是可接受的代价） |

---

## 六、LLM 模型与云服务

LiMo 是模型无关的知识工具，接入多家模型/服务（非开源代码，但同样值得尊重其开放接口）：

| 服务 | 用途 |
|------|------|
| **DeepSeek** | 主力 LLM，思考链（`reasoning_content`）流式展示 |
| **通义千问 Qwen（DashScope）** | LLM + OCR + ASR + 视频理解 |
| **OpenCode Go** | OpenAI 兼容 LLM 网关 |
| **Tavily** | 对话搜索 API（一级） |
| **B 站必剪（bcut）** | 免费云转写 |
| **DuckDuckGo** | 搜索降级链末端 |

---

## 七、合规与尊重承诺

1. **版权归属**：所有第三方代码的版权归原作者所有；本文件仅记录使用与借鉴，不主张任何归属。
2. **许可证合规**：
   - AGPL/GPL 类：ebooklib、html2text 已于 2026-08-26 彻底移除；abogus（GPL-3.0 声明）于 2026-10-06 随自研抖音路径一并移除（**主 exe 零 AGPL/GPL 库**）；pymupdf4llm（AGPL）为**进程外插件**、SearXNG（AGPL）不随主包、ffmpeg（GPLv3）为**独立进程**且随包附全文——均不并入核心主程序。
   - 其余依赖按各自许可证（MIT/BSD/Apache/MPL/ISC 等）自由使用。
3. **不擦除声明**：任何分发/修改都不会剥离源码或二进制中的版权与许可证声明。
4. **完整许可证文本**：各依赖的完整许可证文本随其包发布（pip / npm / cargo），本文件不重复粘贴全文；如需逐条核对，以官方仓库 LICENSE 为准。

> 本文件的许可证标注已按本地安装包元数据核对（2026-08-25，`pip/npm/cargo` 实际安装版本）；个别包同时含核心与绑定两层许可（如 resvg 核心 MPL-2.0 / resvg-py 绑定 MIT），已分别标注。ffmpeg 已按 gyan.dev 官方页面核实为 GPLv3（2026-08-26）。**2026-10-05 复核**：比对 `backend/pyproject.toml` 与 `frontend/package.json`，新增 `beautifulsoup4`（显式化）与 `magika`（传递依赖但需显式保留）两条，其余无变化。任何出入以官方仓库为准。

---

*LiMo 的开发离不开这些优秀项目。我们以谦卑之心学习，以合规之行回报。*
