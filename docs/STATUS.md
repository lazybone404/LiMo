# LiMo 项目状态

> 纯事实清单。设计决策见 [CLAUDE.md](../CLAUDE.md)，分层架构见 [architecture.md](architecture.md)。
> 修改历史见 [CHANGELOG.md](CHANGELOG.md)。

**更新**: 2026-08-04 | **版本**: 0.3.0

---

## 规模

| 指标 | 数量 |
|------|------|
| 后端 Python 文件 | 87 |
| 前端 TS/TSX/CSS 文件 | 91（89 ts/tsx + 2 css） |
| 前端测试文件 | 8（100 个测试用例） |
| API 路由模块 | 14 |
| API 端点 | 70 |
| 适配器 | 5 |
| 引擎 (ASR/Doc/OCR) | 9 内置（+三方插件 pymupdf4llm） |
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

> Video 适配器内含 BilibiliSubtitle / YouTubeSubtitle 字幕优先提取逻辑，不计为独立适配器。

## 引擎

| 类别 | 引擎 | 类型 |
|------|------|------|
| ASR | bcut-asr | 免费云 |
| ASR | dashscope-asr | 云端 |
| Doc | pypdfium2 | 本地（BSD-3 C 引擎） |
| Doc | markitdown | 本地 |
| Doc | python-docx | 本地 |
| Doc | python-pptx | 本地 |
| Doc | openpyxl | 本地 |
| Doc | ebooklib | 本地 |
| Doc (三方插件) | pymupdf4llm | 进程外（AGPL 隔离，[仓库](https://github.com/lazybone404/limo-pymupdf4llm-plugin)，非官方维护） |
| OCR | dashscope-ocr | 云端 |

> Doc 引擎源码已拆分为独立文件：[doc_types.py](../backend/app/engines/doc_types.py)（共享类型）、[doc_markitdown.py](../backend/app/engines/doc_markitdown.py)、[doc_docx.py](../backend/app/engines/doc_docx.py)、[doc_pptx.py](../backend/app/engines/doc_pptx.py)、[doc_excel.py](../backend/app/engines/doc_excel.py)、[docling.py](../backend/app/engines/docling.py)（含 PyPdfium2Engine）、[epub.py](../backend/app/engines/epub.py)。pdfplumber 为 pypdfium2 的表格辅助（不单独注册）。text_only 变体引擎已删除。
>
> 引擎计数方式：`EngineRegistry.register()` 调用次数。ASR 2 + Doc 6 + OCR 1 = 9 内置（插件 pymupdf4llm 动态注册，不计数）。faster-whisper 与 tesseract 已从内置移除（分别需自行安装 / 三方插件模板 `examples/tesseract_plugin.py`）；docling 已移除（2026-08-03，依赖 torch ~2GB 无法打包）。

## 管道

| 管道 | 端点 | 触发 |
|------|------|------|
| 导入 | `POST /api/ingest` + `/stream` | URL/文本/文件 |
| 生成 | `POST /api/generate/{id}` + `/stream` | LLM 生成笔记 |
| 处理 | `POST /api/process/{id}` | 图片 OCR |
| 对话 | `POST /api/chat` + `/chat/stream` | AI Agent |
| 事实验证 | `POST /api/fact-check/{id}` + `/stream` | LLM 验证 |
| 导出 | 7 端点：单文件/全产物/批量/思维导图/自定义/SSE进度流/临时下载 | HTML(代码高亮+GFM+时间戳badge+标题锚点+KaTeX字体内联)/MD(图片绝对URL)/ZIP(3模式)/思维导图(交互版+SVG) |

## 前端组件

| 组件 | 说明 |
|------|------|
| GenPanel | 导入 + 生成选项 |
| DocLibrary | 文档列表/搜索/筛选 |
| PreviewArea | 预览 + 多 tab |
| FloatingAI | AI 对话浮窗（唯一形态；位置/尺寸记忆、内部栏拖拽调宽；含 ChatCore/ChatSidebar/MessageList/MessageItem/ChatInput） |
| SettingsPage | 模型/引擎/配置（overlay，含对话上下文设置 + 模型元数据编辑） |
| TaskPanel | 浮动任务管理（可拖拽、可取消） |
| ExportMenu | 快捷导出（MD/HTML/ZIP/复制） |
| ExportPanel | 自定义导出（tab选择/格式/主题）+ SSE 进度反馈 |
| EngineSelect | 引擎绑定下拉（含"当前引擎"标签） |
| LogPanel | 实时日志查看 |
| OnboardingWizard | 首次启动向导（三屏） |

## 进度

| 域 | 完成 | 总计 | 进度 |
|----|------|------|------|
| 内容导入 | 8 | 8 | 100% |
| 转录 & 引擎 | 8 | 8 | 100% |
| LLM 生成 | 7 | 7 | 100% |
| 图片处理 | 4 | 4 | 100% |
| AI 对话 | 6 | 6 | 100% |
| AI 审核 | 9 | 10 | 90% |
| 导出 | 8 | 9 | 89% |
| 前端 UI/UX | 8 | 8 | 100% |
| 安全 & 运维 | 8 | 8 | 100% |
| **合计** | **66** | **68** | **97%** |

> 进度说明：
> - AI 审核：9/10 — 专业模式（学术/法律/医疗专用搜索源）暂不实施
> - 导出：8/9 — 待完成图片预处理复用（ZIP 导出复用导入时的 optimize_images 结果）
> - 不做的项目：PDF Layer 2（pdfplumber 已成熟提取表格）、VL 帧过滤（成本/收益不成正比）

## 工程质量（2026-07-31 状态）

| 指标 | 状态 |
|------|:--:|
| FastAPI Depends 依赖注入 | ✅ 8/8 路由迁移完成 |
| disconnect_watcher 公共工具 | ✅ 5 端点统一，修复 2 个 bug |
| 错误体系统一（AppError） | ✅ 11 路由，0 处旧格式残留 |
| 前端测试 | ✅ vitest + 100 tests（store/theme/api/latex/streamBuffers/tabRegistry/chatStore/组件） |
| 对话重写 | ✅ 独立 chatStore + 消息层组件树 + 工具气泡/引用/思考折叠（2026-08-04） |
| 对话上下文管理 | ✅ context_window 四层解析 + 预算引擎 + 历史压缩 + token 进度/费用（2026-08-04 C1-C5） |
| AI 浮窗 | ✅ 移除插槽，统一浮窗（位置记忆/边界钳制/内部栏拖拽） |
| 多任务并发 | ✅ processingTasks 统一推导，双层流式缓冲，per-doc 操作互斥，逐任务取消 |
| 并发控制 | ✅ 生成上限 2、导入上限 3，超限 Toast 提示 |
| 引擎文件拆分 | ✅ doc.py → 5 文件 + 删死代码 |
| Store 领域分节 | ✅ 39 字段 53 action 拆 7 slices（2026-08-01） |
| API 客户端按域拆分 | ✅ core + 7 域文件 + barrel，fetchSSE/fetchFileSSE 合并 |
| contentSource 类型绑定 | ✅ `Record<ContentSource>` getter 统一三处映射 |
| PreviewArea 展示拆分 | ✅ 6 个 props 驱动组件（EmptyState 替代 8 处空态） |
| useTaskRunner hook | ✅ retranscribe/process 公共骨架抽取 |
| 组件测试 | ✅ 首个组件测试（CopyContentBtn 3 用例） |
| 引擎配置 UX | ✅ "当前引擎"标签 + "恢复默认"按钮 |
| 公式 LaTeX 分隔符 | ✅ `\[...\]`/`\(...\)` 前端预处理 + Prompt 约束 + 后端管线 |
| 导出 HTML 对齐预览 | ✅ one-dark 代码高亮 + checkbox 任务列表 + 暗色代码块 |
| 首次启动向导 | ✅ 三屏引导（氛围选择 + 组件检测），替代旧 SetupWizard |
| 空状态拖放区 | ✅ PreviewArea 交互式拖放 + 链接输入 |
| 默认 Tab 优化 | ✅ PDF→整理版原文 / 视频→转录 / 图片→原图 |
| 暗色纹理 | ✅ ::after 遮罩 2-3% 透明度保留质感 |
| 转录完成提示 | ✅ Toast 引导用户点击生成按钮 |

## 目录结构

```
backend/app/
  adapters/      5 个适配器
  engines/       9 个内置引擎 + 插件加载器
  routers/       11 个路由模块
  generation/    生成/处理/AI审核
  agent/         LLM Agent 工具
  llm/           LLMService
  core/          配置/类型/注册/错误/disconnect
  storage/       文件系统存储
frontend/src/
  components/    UI 组件（preview/ 下 6 个展示组件拆分）
  store/         Zustand 状态（types.ts + 7 slices + index.ts 组合层）
  api/           API 客户端（core + 7 域文件 + client.ts barrel）
  hooks/         useTaskRunner 等自定义 hook
  lib/           tabRegistry / contentSources / 主题模块
  test/          前端测试（7 个文件，含 1 个组件测试）
docs/            设计文档（8 份）
design/          引擎实测标准
tests/           集成测试（6 个文件）
```
