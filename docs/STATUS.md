# LiMo 项目状态

> 纯事实清单。设计决策见 [CLAUDE.md](../CLAUDE.md)，分层架构见 [architecture.md](architecture.md)。
> 修改历史见 [CHANGELOG.md](CHANGELOG.md)。

**更新**: 2026-07-13 | **版本**: 0.1.3

---

## 规模

| 指标 | 数量 |
|------|------|
| 后端 Python 文件 | 62 |
| 前端 TS/TSX 文件 | 41 |
| API 路由模块 | 11 |
| API 端点 | ~60 |
| 适配器 | 7 |
| 引擎 (ASR/Doc/OCR) | 17 |
| LLM Agent 工具 | 5 |
| 设计文档 | 8 |

---

## 适配器

| # | 适配器 | 输入类型 | 引擎依赖 |
|---|--------|---------|---------|
| 1 | Video | URL/文件 (.mp4/.mkv/...) | ASR |
| 2 | Document | URL/文件 (.pdf/.docx/.pptx/.xlsx/.epub) | Doc (+ OCR for 扫描件) |
| 3 | Image | URL/文件 (.png/.jpg/...) | OCR |
| 4 | Webpage | URL (http/https) | 无 |
| 5 | Text | 纯文本 | 无 |
| 6 | BilibiliSubtitle | B站 URL (字幕提取) | 无 |
| 7 | YouTubeSubtitle | YouTube URL (字幕提取) | 无 |

## 引擎

| 类别 | 引擎 | 类型 |
|------|------|------|
| ASR | bcut-asr | 免费云 |
| ASR | dashscope-asr | 云端 |
| ASR | faster-whisper-small | 本地 |
| ASR | faster-whisper-medium | 本地 |
| Doc | pymupdf / pymupdf-text / pymupdf4llm | 本地 |
| Doc | pdf-ocr | 本地+云端 |
| Doc | python-docx / python-docx-text | 本地 |
| Doc | python-pptx / python-pptx-text | 本地 |
| Doc | openpyxl | 本地 |
| Doc | markitdown | 本地 |
| Doc | ebooklib | 本地 |
| OCR | dashscope-ocr | 云端 |
| OCR | tesseract | 本地 |

**引擎计数方式**：`EngineRegistry.register()` 调用次数。ASR 4 + Doc 11 + OCR 2 = 17。

## 管道

| 管道 | 端点 | 触发 |
|------|------|------|
| 导入 | `POST /api/ingest` + `/stream` | URL/文本/文件 |
| 生成 | `POST /api/generate/{id}` + `/stream` | LLM 生成笔记 |
| 处理 | `POST /api/process/{id}` | 图片 OCR |
| 对话 | `POST /api/chat` + `/chat/stream` | AI Agent |
| 事实验证 | `POST /api/fact-check/{id}` + `/stream` | LLM 验证 |
| 导出 | 6 端点：单文件/全产物/批量/思维导图/自定义 | HTML(代码高亮+GFM+时间戳badge+标题锚点+KaTeX字体内联)/MD(图片绝对URL)/ZIP(3模式)/思维导图(交互版+SVG) |

## 前端组件

| 组件 | 说明 |
|------|------|
| GenPanel | 导入 + 生成选项 |
| DocLibrary | 文档列表/搜索/筛选 |
| PreviewArea | 预览 + 多 tab |
| AIPanel | AI 对话面板 |
| SettingsPage | 模型/引擎/配置（overlay） |
| TaskPanel | 浮动任务管理（可拖拽、可取消） |
| ExportMenu | 快捷导出（MD/HTML/ZIP/复制） |
| ExportPanel | 自定义导出（tab选择/格式/主题） |
| LogPanel | 实时日志查看 |
| SetupWizard | 首次启动向导 |

## 进度

| 域 | 完成 | 总计 | 进度 |
|----|------|------|------|
| 内容导入 | 8 | 8 | 100% |
| 转录 & 引擎 | 8 | 10 | 80% |
| LLM 生成 | 7 | 7 | 100% |
| 图片处理 | 4 | 4 | 100% |
| AI 对话 | 5 | 5 | 100% |
| 事实验证 | 4 | 5 | 80% |
| 导出 | 7 | 8 | 88% |
| 前端 UI/UX | 8 | 8 | 100% |
| 安全 & 运维 | 8 | 8 | 100% |
| **合计** | **59** | **63** | **94%** |

> 进度说明：
> - 转录 & 引擎：8/10 — 待完成 VL 帧过滤（PaddleOCR 文字区域检测）、PDF Layer 2（表格修正/数字化/理解）
> - 事实验证：4/5 — 待完成报告可交互（点击来源链接查看原文）
> - 导出：7/8 — 待完成图片预处理复用（ZIP 导出复用导入时的 optimize_images 结果）

## 目录结构

```
backend/app/
  adapters/      7 个适配器
  engines/       17 个引擎
  routers/       11 个路由模块
  generation/    生成/处理/事实验证
  agent/         LLM Agent 工具
  llm/           LLMService
  core/          配置/类型/注册/错误
  storage/       文件系统存储
frontend/src/
  components/    UI 组件
  store/         Zustand 状态
  api/           API 客户端
docs/            设计文档（8 份）
design/          引擎实测标准
tests/           集成测试
```
