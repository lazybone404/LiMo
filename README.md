# LiMo（沥墨）

粘贴链接或拖入文件，自动提取正文、语音转文字、生成 Markdown 笔记。

---

## 能做什么

### 导入内容

把网址、本地文件、纯文本粘贴进去就行。支持这些格式：

- **网页**：贴 URL，自动提取正文
- **视频**：B 站、YouTube，或本地 mp4/mkv。自动下字幕或语音转文字
- **文档**：PDF、Word、PPT、Excel、EPUB。导入即转录，自动提取文字和表格
- **图片**：PNG、JPG 等，OCR 识别文字
- **文本**：直接粘贴 Markdown 或纯文本

文件可以直接拖进窗口，最大支持 2GB。

### 转录引擎

系统内置轻量转录引擎，开箱即用：

语音转文字（ASR）：bcut-asr（B 站免费，无需 Key）、dashscope-asr（阿里云）

文档提取（Doc）：pypdfium2（PDF 默认，BSD-3）、markitdown（Office/EPUB 通用）、python-docx/pptx（Word/PPT）、openpyxl（Excel）、epub-container（EPUB 章节提取，自研）

图片识别（OCR）：dashscope-ocr（阿里云，需 Key）

需要更强的 PDF 解析（文字+图片+矢量图完整提取）？装**三方插件** pymupdf4llm：从[插件仓库](https://github.com/lazybone404/limo-pymupdf4llm-plugin)下载 zip → 设置页「插件」→「**安装插件**」选 zip → 重启 → **一键安装**依赖即可。

引擎可以在设置里换，每种文件类型独立绑定。切换后点「重新转录」重新提取。

### 生成笔记

转录完成后点「生成」，AI 会同时产出两份 Markdown：

- **笔记**：结构化整理，可选目录、时间戳、公式集、参考文献等
- **整理版原文**：修正错别字、去掉口语词，适合存档

视频可以开启画面理解——AI 边看画面边写笔记，比如 PPT 里的图表、代码演示。Excel 生成数据摘要（概况、指标、字段解读）。

生成笔记时可以附上参考资料（如视频评论区观点、你想让 AI 审阅的材料）——参考资料作为背景注入笔记生成，与原文冲突时以原文/模型判断为准。

生成时可以看到实时输出，支持中途取消、断点续写。同模式保留最近两版，可以来回切换。

### 对话

选中文档后可以和 AI 聊。AI 能搜你的文档库、查网络、打开链接——不用来回切换。

### 事实验证

检查笔记里的说法是否靠谱。AI 提取所有事实断言 → 网上搜证据 → 逐条比对，出验证报告。

### 导出

- Markdown：一键下载，图片不会裂
- HTML：一个文件包含全部，KaTeX 公式、代码高亮，断网也能看
- ZIP：打包所有产物和图片
- 思维导图：交互式 HTML 或 SVG
- 对话记录：Markdown + JSON

预览区右上角可以快捷导出，顶部 Download 可以批量导出多篇。

---

## 安装

### Windows 桌面版

从 [Releases](https://github.com/lazybone404/LiMo/releases) 下载安装包，双击安装。自带 ffmpeg、SearXNG 搜索引擎，不需要装 Python。OCR 用阿里云 dashscope（云端，需 API Key）；更强的 PDF 解析可装三方插件。

### 开发环境

Python 3.12+、Node.js 18+。

```bash
bash start.sh
```

浏览器打开 `http://localhost:5173`。

---

## 首次使用

第一次启动会弹出**三屏引导向导**：① 选阅读氛围（主题一键包）→ ② 系统组件检测（ffmpeg/后端就绪）→ ③ AI 配置（填 LLM / DashScope Key，已配好的步骤自动跳过，可整屏跳过）。之后可随时在设置里改。

打开设置，在「模型 & 引擎」→「LLM 厂商」里填 API Key——DeepSeek、OpenAI、DashScope 都行。然后导入内容试试。

**缺 key 会主动提醒**：导入图片/视频缺 DashScope 时，导入区会出现可关闭的内联提示条；发对话前缺 LLM Key 会先弹一次提示。不再「点了没反应」。

转录引擎内置：bcut-asr（B 站免费）与 pypdfium2（PDF 解析）无需 Key；dashscope（云端 OCR/ASR）需 API Key。搜索引擎第一次用会自动启动。
